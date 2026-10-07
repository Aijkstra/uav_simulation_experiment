import uvicorn

from .api import create_app
from .config import ConfigurationError


def run() -> None:
    try:
        app = create_app()
    except ConfigurationError as error:
        raise SystemExit("配置错误: %s" % error) from error
    uvicorn.run(app, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    run()
