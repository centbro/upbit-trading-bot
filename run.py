import uvicorn
import os
from dotenv import load_dotenv

load_dotenv()

if __name__ == "__main__":
    uvicorn.run(
        "app:app",
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", 8000)),
        workers=1,          # 인메모리 공유 상태 — 반드시 1
        log_level="info",
        access_log=True,
        reload=False,       # 프로덕션: 파일 감시 비활성
    )
