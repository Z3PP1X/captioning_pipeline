from pipeline.db import DB
from pipeline.config import get_settings

db = DB(get_settings().db_path)

with db.write() as conn:
    # 1. Clear error on successfully finished jobs
    conn.execute("UPDATE jobs SET error=NULL WHERE status='finished' AND error IS NOT NULL")
    
    # 2. Reset videos that were in extracting_750 back to chunked if no picks exist
    conn.execute("""
        UPDATE videos SET status='chunked'
        WHERE status='extracting_750'
        AND id NOT IN (SELECT DISTINCT video_id FROM frames)
    """)

print("Cleaned up job errors and reset interrupted extracting_750 videos back to 'chunked'.")
