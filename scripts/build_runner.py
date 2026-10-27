"""Build only the project's reviewed sandbox context, then record its immutable image ID."""

import json
import subprocess

from app.tools.runner import IMAGE_INPUTS, PROJECT_ROOT, SANDBOX
from app.tools.runner_artifacts import RunnerError, file_hashes


def main() -> int:
    output = PROJECT_ROOT / "data"
    output.mkdir(exist_ok=True)
    iidfile = output / "runner-image-id.txt"
    input_hashes = file_hashes(SANDBOX, IMAGE_INPUTS)
    subprocess.run(
        [
            "docker",
            "build",
            "--platform=linux/amd64",
            "--file",
            str(SANDBOX / "Dockerfile.python-demo"),
            "--iidfile",
            str(iidfile),
            "--tag",
            "graft-python-demo:phase2",
            str(SANDBOX),
        ],
        check=True,
    )
    image_id = iidfile.read_text().strip()
    if input_hashes != file_hashes(SANDBOX, IMAGE_INPUTS):
        raise RunnerError("Runner inputs changed during the build. Rebuild before running tests.")
    receipt = {
        "image_id": image_id,
        "input_hashes": input_hashes,
    }
    receipt_path = output / "runner-image.json"
    receipt_path.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    print(f"Prepared {image_id}\nReceipt: {receipt_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
