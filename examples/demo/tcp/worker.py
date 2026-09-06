from demo.common.worker import run
from .business import create_sut, start_manual


if __name__ == "__main__":
    run(create_sut, start_manual)
