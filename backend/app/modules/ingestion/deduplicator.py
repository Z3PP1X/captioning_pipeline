"""Perceptual hashing (64-bit pHash, 64-bit dHash, 24-bin HSV histogram) and O(N log N) BK-Tree clustering.

Implements Module A.3 of the Architecture Blueprint:
- Vectorized 64-bit DCT perceptual hash (`compute_phash`, `compute_phash_u64`)
- Vectorized 64-bit gradient difference hash (`compute_dhash`, `compute_dhash_u64`)
- 24-bin L2-normalized HSV color histogram (`compute_hsv_histogram`, `hsv_cosine_similarity`)
- Pass 1: O(N) Temporal Window Stream pre-collapsing consecutive frames
- Pass 2: Multi-Index 16-bit Band Prefix Bucketing + Burkhard-Keller Metric Tree (`BKTree`)
  over discrete 64-bit Hamming distance d_H(h1, h2) = popcount(h1 ^ h2)
- Union-Find cluster formation and automatic Best-Representative Selection (argmax Q)
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Sequence

import cv2
import numpy as np

from backend.app.models.domain import (
    ClusterAssignment,
    ClusterRecord,
    CurationState,
    MediaFingerprint,
)


def compute_phash_u64(img: np.ndarray) -> int:
    """Compute 64-bit DCT perceptual hash as an unsigned 64-bit Python int."""
    gray = img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    small = cv2.resize(gray, (32, 32), interpolation=cv2.INTER_AREA).astype(np.float32)
    dct_low = cv2.dct(small)[:8, :8]
    med = float(np.median(dct_low))
    bits = (dct_low > med).ravel()
    packed = np.packbits(bits)
    return int.from_bytes(packed.tobytes(), byteorder="big", signed=False)


def compute_phash(img: np.ndarray) -> str:
    """Compute 64-bit DCT perceptual hash formatted as a 16-character hex string."""
    return f"{compute_phash_u64(img):016x}"


def compute_dhash_u64(img: np.ndarray) -> int:
    """Compute 64-bit horizontal gradient difference hash as an unsigned 64-bit Python int."""
    gray = img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    small = cv2.resize(gray, (9, 8), interpolation=cv2.INTER_AREA)
    bits = (small[:, 1:] > small[:, :-1]).ravel()
    packed = np.packbits(bits)
    return int.from_bytes(packed.tobytes(), byteorder="big", signed=False)


def compute_dhash(img: np.ndarray) -> str:
    """Compute 64-bit horizontal gradient difference hash as a 16-character hex string."""
    return f"{compute_dhash_u64(img):016x}"


def hamming_distance_u64(h1: int, h2: int) -> int:
    """Fast hardware popcount Hamming distance between two 64-bit unsigned ints."""
    return (h1 ^ h2).bit_count()


def hamming_distance_hex(h1: str, h2: str) -> int:
    """Hamming distance between two 16-char hex hash strings."""
    return (int(h1, 16) ^ int(h2, 16)).bit_count()


def compute_hsv_histogram(img_bgr: np.ndarray) -> np.ndarray:
    """Compute 24-bin L2-normalized HSV color histogram (8 H + 8 S + 8 V) in float32."""
    if img_bgr.ndim == 2:
        img_bgr = cv2.cvtColor(img_bgr, cv2.COLOR_GRAY2BGR)
    elif img_bgr.shape[2] == 4:
        img_bgr = cv2.cvtColor(img_bgr, cv2.COLOR_BGRA2BGR)

    h, w = img_bgr.shape[:2]
    if max(h, w) > 64:
        small = cv2.resize(img_bgr, (64, 64), interpolation=cv2.INTER_AREA)
    else:
        small = img_bgr

    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    hist_h = cv2.calcHist([hsv], [0], None, [8], [0, 180]).ravel()
    hist_s = cv2.calcHist([hsv], [1], None, [8], [0, 256]).ravel()
    hist_v = cv2.calcHist([hsv], [2], None, [8], [0, 256]).ravel()

    vec = np.concatenate([hist_h, hist_s, hist_v]).astype(np.float32)
    norm = float(np.linalg.norm(vec))
    if norm > 1e-8:
        vec /= norm
    return vec


def hsv_cosine_similarity(
    hist1: np.ndarray | bytes | None,
    hist2: np.ndarray | bytes | None,
) -> float:
    """Compute cosine similarity in [0, 1] between two L2-normalized 24-bin HSV histograms."""
    if hist1 is None or hist2 is None:
        return 1.0
    v1 = np.frombuffer(hist1, dtype=np.float32) if isinstance(hist1, (bytes, bytearray, memoryview)) else hist1
    v2 = np.frombuffer(hist2, dtype=np.float32) if isinstance(hist2, (bytes, bytearray, memoryview)) else hist2
    if v1.size != 24 or v2.size != 24:
        return 1.0
    return float(np.clip(np.dot(v1, v2), 0.0, 1.0))


def compute_fingerprint(img_bgr: np.ndarray) -> MediaFingerprint:
    """Compute complete multi-hash fingerprint (64-bit pHash, 64-bit dHash, 24-bin HSV histogram) in one pass."""
    gray = img_bgr if img_bgr.ndim == 2 else cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    phash_u64 = compute_phash_u64(gray)
    dhash_u64 = compute_dhash_u64(gray)
    hsv_vec = compute_hsv_histogram(img_bgr)
    return MediaFingerprint(
        phash_hex=f"{phash_u64:016x}",
        phash_u64=phash_u64,
        dhash_hex=f"{dhash_u64:016x}",
        dhash_u64=dhash_u64,
        hsv_hist=hsv_vec.tobytes(),
    )


class UnionFind:
    """Disjoint-set (Union-Find) data structure with path compression and union by rank."""

    __slots__ = ("parent", "rank")

    def __init__(self, n: int) -> None:
        self.parent = list(range(n))
        self.rank = [0] * n

    def find(self, i: int) -> int:
        path = []
        root = i
        while self.parent[root] != root:
            path.append(root)
            root = self.parent[root]
        for node in path:
            self.parent[node] = root
        return root

    def union(self, i: int, j: int) -> bool:
        ri = self.find(i)
        rj = self.find(j)
        if ri == rj:
            return False
        if self.rank[ri] < self.rank[rj]:
            ri, rj = rj, ri
        self.parent[rj] = ri
        if self.rank[ri] == self.rank[rj]:
            self.rank[ri] += 1
        return True


@dataclass(slots=True)
class _BKNode:
    """Internal node for Burkhard-Keller metric tree over 64-bit Hamming space."""

    hash_u64: int
    item_indices: list[int] = field(default_factory=list)
    children: dict[int, _BKNode] = field(default_factory=dict)


class BKTree:
    """Burkhard-Keller metric tree over discrete 64-bit Hamming distance.

    Deduplicates identical 64-bit hashes into a single node and uses the triangle
    inequality |d(q, node) - edge_dist| <= radius to prune subtrees during range queries.
    """

    __slots__ = ("root", "unique_nodes", "total_items", "distance_computations")

    def __init__(self) -> None:
        self.root: _BKNode | None = None
        self.unique_nodes: int = 0
        self.total_items: int = 0
        self.distance_computations: int = 0

    def add(self, hash_u64: int, item_index: int) -> None:
        """Insert a 64-bit hash and its item index into the BK-tree."""
        self.total_items += 1
        if self.root is None:
            self.root = _BKNode(hash_u64=hash_u64, item_indices=[item_index])
            self.unique_nodes = 1
            return

        curr = self.root
        while True:
            self.distance_computations += 1
            dist = (hash_u64 ^ curr.hash_u64).bit_count()
            if dist == 0:
                curr.item_indices.append(item_index)
                return
            child = curr.children.get(dist)
            if child is None:
                curr.children[dist] = _BKNode(hash_u64=hash_u64, item_indices=[item_index])
                self.unique_nodes += 1
                return
            curr = child

    def search(self, query_u64: int, radius: int) -> list[tuple[int, int]]:
        """Return all `(item_index, hamming_distance)` pairs within `radius` bits of `query_u64`."""
        if self.root is None:
            return []

        matches: list[tuple[int, int]] = []
        stack: list[_BKNode] = [self.root]

        while stack:
            node = stack.pop()
            self.distance_computations += 1
            dist = (query_u64 ^ node.hash_u64).bit_count()
            if dist <= radius:
                for idx in node.item_indices:
                    matches.append((idx, dist))

            low = dist - radius
            high = dist + radius
            for edge_d, child in node.children.items():
                if low <= edge_d <= high:
                    stack.append(child)

        return matches


class MultiIndexPrefixBuckets:
    """4-band 16-bit Multi-Index Hashing (MIH) table for fast sub-linear candidate generation.

    Partitions each 64-bit hash into 4 disjoint 16-bit bands:
        Band 0: bits 63..48
        Band 1: bits 47..32
        Band 2: bits 31..16
        Band 3: bits 15..0
    By the Pigeonhole Principle, any two hashes with Hamming distance <= 3 MUST share at least
    one exact 16-bit band, and any two hashes with Hamming distance <= 7 MUST share at least
    one 16-bit band within 1 bit.
    """

    __slots__ = ("bands",)

    def __init__(self) -> None:
        self.bands: tuple[dict[int, list[int]], ...] = (
            defaultdict(list),
            defaultdict(list),
            defaultdict(list),
            defaultdict(list),
        )

    @staticmethod
    def extract_bands(hash_u64: int) -> tuple[int, int, int, int]:
        return (
            (hash_u64 >> 48) & 0xFFFF,
            (hash_u64 >> 32) & 0xFFFF,
            (hash_u64 >> 16) & 0xFFFF,
            hash_u64 & 0xFFFF,
        )

    def add(self, hash_u64: int, rep_index: int) -> None:
        b0, b1, b2, b3 = self.extract_bands(hash_u64)
        self.bands[0][b0].append(rep_index)
        self.bands[1][b1].append(rep_index)
        self.bands[2][b2].append(rep_index)
        self.bands[3][b3].append(rep_index)

    def query_candidates(self, hash_u64: int, max_band_errors: int = 1) -> set[int]:
        """Return candidate unique-hash indices sharing a band within `max_band_errors` bits."""
        band_vals = self.extract_bands(hash_u64)
        candidates: set[int] = set()
        for b_idx, b_val in enumerate(band_vals):
            table = self.bands[b_idx]
            exact = table.get(b_val)
            if exact:
                candidates.update(exact)
            if max_band_errors >= 1:
                for bit in range(16):
                    flipped = b_val ^ (1 << bit)
                    near = table.get(flipped)
                    if near:
                        candidates.update(near)
        return candidates


def _extract_item_fields(item: Any, idx: int) -> dict[str, Any]:
    """Normalize dict or dataclass/object media item into a uniform clustering dictionary."""
    if isinstance(item, dict):
        raw_id = item.get("id") or item.get("item_id") or idx
        item_id = int(raw_id)
        phash_str = item.get("phash") or item.get("phash_hex") or "0000000000000000"
        dhash_str = item.get("dhash") or item.get("dhash_hex") or phash_str
        phash_u64 = item.get("phash_u64")
        if phash_u64 is None:
            phash_u64 = int(phash_str, 16)
        dhash_u64 = item.get("dhash_u64")
        if dhash_u64 is None:
            dhash_u64 = int(dhash_str, 16)
        hsv_hist = item.get("hsv_hist")
        quality_score = float(item.get("quality_score", 0.5))
        sharpness_score = float(item.get("sharpness_score", quality_score))
        curation_state = str(item.get("curation_state", "candidate"))
        passed_gates = bool(item.get("passed_gates", curation_state != "auto_rejected"))
        timestamp_s = item.get("timestamp_s")
        scene_index = int(item.get("scene_index", 0) or 0)
    else:
        item_id = int(getattr(item, "item_id", getattr(item, "id", idx)))
        phash_str = getattr(item, "phash", "0000000000000000")
        dhash_str = getattr(item, "dhash", phash_str)
        phash_u64 = int(phash_str, 16)
        dhash_u64 = int(dhash_str, 16)
        hsv_hist = getattr(item, "hsv_hist", None)
        q_obj = getattr(item, "quality", None)
        if q_obj is not None:
            quality_score = float(getattr(q_obj, "quality_score", 0.5))
            sharpness_score = float(getattr(q_obj, "sharpness_score", quality_score))
            passed_gates = bool(getattr(q_obj, "passed_gates", True))
        else:
            quality_score = float(getattr(item, "quality_score", 0.5))
            sharpness_score = float(getattr(item, "sharpness_score", quality_score))
            passed_gates = getattr(item, "curation_state", "candidate") != "auto_rejected"
        curation_state = str(getattr(item, "curation_state", "candidate"))
        timestamp_s = getattr(item, "timestamp_s", None)
        scene_index = int(getattr(item, "scene_index", 0) or 0)

    hsv_vec: np.ndarray | None = None
    if hsv_hist is not None:
        if isinstance(hsv_hist, (bytes, bytearray, memoryview)):
            arr = np.frombuffer(hsv_hist, dtype=np.float32)
            if arr.size == 24:
                hsv_vec = arr
        elif isinstance(hsv_hist, np.ndarray) and hsv_hist.size == 24:
            hsv_vec = hsv_hist.astype(np.float32, copy=False)

    return {
        "idx": idx,
        "item_id": item_id,
        "phash_hex": f"{int(phash_u64):016x}",
        "phash_u64": int(phash_u64),
        "dhash_hex": f"{int(dhash_u64):016x}",
        "dhash_u64": int(dhash_u64),
        "hsv_vec": hsv_vec,
        "quality_score": quality_score,
        "sharpness_score": sharpness_score,
        "passed_gates": passed_gates,
        "curation_state": curation_state,
        "timestamp_s": float(timestamp_s) if timestamp_s is not None else None,
        "scene_index": scene_index,
    }


def _pair_matches(
    a: dict[str, Any],
    b: dict[str, Any],
    phash_threshold: int,
    dhash_threshold: int | None,
    hsv_min_cosine: float,
) -> bool:
    """Verify if two items satisfy the composite pHash, dHash, and HSV similarity criteria."""
    dp = (a["phash_u64"] ^ b["phash_u64"]).bit_count()
    if dp > phash_threshold:
        return False

    if dhash_threshold is not None and dhash_threshold >= 0:
        dd = (a["dhash_u64"] ^ b["dhash_u64"]).bit_count()
        if dd > dhash_threshold:
            return False

    if hsv_min_cosine > 0.0 and a["hsv_vec"] is not None and b["hsv_vec"] is not None:
        cos_sim = float(np.dot(a["hsv_vec"], b["hsv_vec"]))
        if cos_sim < hsv_min_cosine:
            return False

    return True


def _finalize_clusters(
    norm_items: list[dict[str, Any]],
    uf: UnionFind,
    distance_computations: int,
) -> tuple[list[ClusterRecord], list[ClusterAssignment], dict[str, Any]]:
    """Build ClusterRecord and ClusterAssignment objects from Union-Find components."""
    groups: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for entry in norm_items:
        root = uf.find(entry["idx"])
        groups[root].append(entry)

    clusters: list[ClusterRecord] = []
    assignments_by_idx: dict[int, ClusterAssignment] = {}
    duplicates_collapsed = 0

    for cluster_num, members in enumerate(groups.values(), start=1):
        # Prefer candidates that passed hard quality gates; rank by quality_score DESC, sharpness DESC, earlier idx
        valid_members = [m for m in members if m["passed_gates"] and m["curation_state"] != "auto_rejected"]
        pool = valid_members if valid_members else members

        best_member = max(
            pool,
            key=lambda m: (
                m["quality_score"],
                m["sharpness_score"],
                -m["idx"],
            ),
        )
        rep_id = best_member["item_id"]
        rep_phash_u64 = best_member["phash_u64"]

        clusters.append(
            ClusterRecord(
                cluster_id=cluster_num,
                representative_item_id=rep_id,
                centroid_phash=best_member["phash_hex"],
                centroid_dhash=best_member["dhash_hex"],
                member_ids=[m["item_id"] for m in members],
                member_count=len(members),
                max_quality_score=round(float(best_member["quality_score"]), 4),
                scene_index=best_member["scene_index"],
            )
        )

        for m in members:
            is_rep = m["idx"] == best_member["idx"]
            dist = (m["phash_u64"] ^ rep_phash_u64).bit_count()
            if not m["passed_gates"] or m["curation_state"] == "auto_rejected":
                state: CurationState = "auto_rejected" if not is_rep else (
                    "keep" if m["passed_gates"] else "auto_rejected"
                )
                if not is_rep:
                    state = "duplicate_hidden"
                    duplicates_collapsed += 1
            elif is_rep:
                state = "star" if m["curation_state"] == "star" else "keep"
            else:
                state = "duplicate_hidden"
                duplicates_collapsed += 1

            assignments_by_idx[m["idx"]] = ClusterAssignment(
                item_id=m["item_id"],
                cluster_id=cluster_num,
                is_cluster_representative=is_rep,
                curation_state=state,
                distance_to_representative=dist,
            )

    ordered_assignments = [assignments_by_idx[i] for i in range(len(norm_items))]
    stats = {
        "total_items": len(norm_items),
        "clusters_count": len(clusters),
        "representatives_kept": len(clusters),
        "duplicates_collapsed": duplicates_collapsed,
        "distance_computations": distance_computations,
    }
    return clusters, ordered_assignments, stats


def cluster_media_items(
    items: Sequence[Any],
    *,
    phash_threshold: int = 6,
    dhash_threshold: int | None = 8,
    hsv_min_cosine: float = 0.85,
    temporal_window_pass: bool = True,
    index_strategy: str = "auto",
) -> tuple[list[ClusterRecord], list[ClusterAssignment], dict[str, Any]]:
    """Fast O(N log N) perceptual hash & HSV color clustering with Best-Representative Selection.

    Algorithm:
    1. Pass 1 (Temporal Adjacency Stream): Consecutive frames within the same scene/timeline
       matching pHash/dHash/HSV thresholds are unioned in O(N).
    2. Pass 2 (Multi-Index 16-bit Prefix Bucketing + BK-Tree):
       - `index_strategy="mih"` or `"auto"` (for threshold <= 7): 4-band 16-bit Multi-Index
         Hashing (Pigeonhole Principle) retrieves exact and 1-bit band neighbors in O(1)
         amortized time per hash.
       - `index_strategy="bktree"` or `"auto"` (for threshold > 7): Burkhard-Keller metric
         tree (`BKTree`) deduplicates identical hashes into single tree nodes and prunes the
         64-bit Hamming space via the triangle inequality in O(U log U) time.
    3. Best-Representative Selection: Within each connected cluster, selects `argmax Q(I)` as
       representative (`is_cluster_representative=True`, `curation_state='keep'`) and marks all
       other cluster members `duplicate_hidden`.
    """
    n = len(items)
    if n == 0:
        return [], [], {
            "total_items": 0,
            "clusters_count": 0,
            "representatives_kept": 0,
            "duplicates_collapsed": 0,
            "distance_computations": 0,
        }

    norm_items = [_extract_item_fields(item, idx) for idx, item in enumerate(items)]
    uf = UnionFind(n)
    dist_ops = 0

    # Pass 1: O(N) Temporal Window Stream (collapses adjacent duplicate video frames immediately)
    if temporal_window_pass and n >= 2:
        for i in range(1, n):
            prev = norm_items[i - 1]
            curr = norm_items[i]
            if prev["scene_index"] == curr["scene_index"]:
                dist_ops += 1
                if _pair_matches(curr, prev, phash_threshold, dhash_threshold, hsv_min_cosine):
                    uf.union(i - 1, i)

    use_mih = (index_strategy == "mih") or (index_strategy == "auto" and phash_threshold <= 7)

    if use_mih:
        mih = MultiIndexPrefixBuckets()
        max_band_err = 0 if phash_threshold <= 3 else 1
        for idx, entry_i in enumerate(norm_items):
            h_u64 = entry_i["phash_u64"]
            candidate_indices = mih.query_candidates(h_u64, max_band_errors=max_band_err)
            for cand_idx in candidate_indices:
                if uf.find(idx) == uf.find(cand_idx):
                    continue
                dist_ops += 1
                if _pair_matches(entry_i, norm_items[cand_idx], phash_threshold, dhash_threshold, hsv_min_cosine):
                    uf.union(idx, cand_idx)
            mih.add(h_u64, idx)
        total_dist_ops = dist_ops
    else:
        bktree = BKTree()
        for idx, entry_i in enumerate(norm_items):
            h_u64 = entry_i["phash_u64"]
            matches = bktree.search(h_u64, phash_threshold)
            for cand_idx, _ in matches:
                if uf.find(idx) == uf.find(cand_idx):
                    continue
                if _pair_matches(entry_i, norm_items[cand_idx], phash_threshold, dhash_threshold, hsv_min_cosine):
                    uf.union(idx, cand_idx)
            bktree.add(h_u64, idx)
        total_dist_ops = dist_ops + bktree.distance_computations

    return _finalize_clusters(norm_items, uf, total_dist_ops)


def cluster_naive_pairwise(
    items: Sequence[Any],
    *,
    phash_threshold: int = 6,
    dhash_threshold: int | None = 8,
    hsv_min_cosine: float = 0.85,
) -> tuple[list[ClusterRecord], list[ClusterAssignment], dict[str, Any]]:
    """Reference O(N^2) pairwise clustering implementation for benchmark comparison & verification."""
    n = len(items)
    if n == 0:
        return [], [], {
            "total_items": 0,
            "clusters_count": 0,
            "representatives_kept": 0,
            "duplicates_collapsed": 0,
            "distance_computations": 0,
        }

    norm_items = [_extract_item_fields(item, idx) for idx, item in enumerate(items)]
    uf = UnionFind(n)
    dist_ops = 0

    for i in range(n):
        ei = norm_items[i]
        for j in range(i + 1, n):
            dist_ops += 1
            if _pair_matches(ei, norm_items[j], phash_threshold, dhash_threshold, hsv_min_cosine):
                uf.union(i, j)

    return _finalize_clusters(norm_items, uf, dist_ops)
