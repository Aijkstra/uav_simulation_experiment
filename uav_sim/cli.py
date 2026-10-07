import argparse
import json
from dataclasses import asdict

from .config import load_experiment_data
from .simulator import Simulator


def main() -> None:
    parser = argparse.ArgumentParser(description="运行UAV单次仿真")
    parser.add_argument("--trial", type=int, default=1)
    parser.add_argument("--action", choices=["release", "delay", "reroute"], default="release")
    parser.add_argument("--frames", action="store_true", help="输出完整逐帧轨迹")
    args = parser.parse_args()
    result = Simulator(load_experiment_data()).run(args.trial, args.action, include_frames=args.frames)
    payload = asdict(result)
    if not args.frames:
        payload.pop("frames", None)
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
