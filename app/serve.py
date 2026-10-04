"""Cloud entrypoint; fail before listening when production settings are missing."""
import os
import uvicorn
from app.deployment_check import main as check


if __name__ == '__main__':
    if check():
        raise SystemExit(1)
    uvicorn.run('app.web:create_web_app', factory=True, host='0.0.0.0', port=int(os.getenv('PORT', '8000')),
                access_log=False, workers=1)
