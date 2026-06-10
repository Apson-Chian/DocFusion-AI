import os

import uvicorn

from backend.app.core.env import load_project_env


load_project_env()


def main():
    host = os.getenv("DOCFUSION_HOST"
                     , "127.0.0.1")
    port = int(os.getenv("DOCFUSION_PORT", "8000"))
    uvicorn.run("backend.app.main:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
