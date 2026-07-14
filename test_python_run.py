import os
import sys


def main() -> None:
    print("Python test OK")
    print(f"executable: {sys.executable}")
    print(f"version: {sys.version.split()[0]}")
    print(f"cwd: {os.getcwd()}")


if __name__ == "__main__":
    main()
