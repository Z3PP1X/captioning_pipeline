import av
print("av.error:", [x for x in dir(av.error) if "Error" in x or "Exception" in x or "FFmpeg" in x])
