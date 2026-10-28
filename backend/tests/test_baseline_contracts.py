import copy
import json

import pytest
from pydantic import ValidationError

from app.baseline_contracts import BaselineBinding


def w1_record():
    return {
        "schema_version": "1",
        "run_id": "pinned-run",
        "target_repo_id": "crawler-demo",
        "target_sha": "a" * 40,
        "target_path_prefix": "examples/crawler-target",
        "exported_files": {"src/crawler.py": "b" * 64},
        "acceptance_files": {"test_retries.py": "c" * 64},
        "runner_id": "crawler-demo-pytest-v1",
        "image_id": "sha256:" + "d" * 64,
        "image_receipt_sha256": "e" * 64,
        "runner_definition_hashes": {"backend/app/pinned_baseline.py": "f" * 64},
        "captures": [
            {
                "suite": suite,
                "artifact_id": f"test-captures/{suite}/capture.json",
                "sha256": "1" * 64,
                "artifact_hashes": {
                    path: "2" * 64 for path in ("stdout.txt", "stderr.txt", "results/junit.xml")
                },
            }
            for suite in ("isolation", "baseline", "acceptance")
        ],
        "inputs_unchanged": True,
    }


def test_w1_hash_maps_become_immutable_typed_manifests_without_losing_identity():
    record = w1_record()
    original = copy.deepcopy(record)
    binding = BaselineBinding.from_w1(record)
    assert record == original
    assert {item.path: item.sha256 for item in binding.exported_files} == record["exported_files"]
    assert binding.run_id == record["run_id"]
    assert binding.target_sha == record["target_sha"]
    assert binding.target_path_prefix == record["target_path_prefix"]
    assert BaselineBinding.model_validate_json(binding.model_dump_json()) == binding
    assert isinstance(json.loads(binding.model_dump_json())["exported_files"], list)
    record["exported_files"].clear()
    record["captures"][0]["artifact_hashes"].clear()
    assert len(binding.exported_files) == 1 and len(binding.captures[0].artifacts) == 3
    with pytest.raises(AttributeError):
        binding.captures.append("new")
    with pytest.raises(ValidationError, match="frozen"):
        binding.captures[0].artifacts[0].sha256 = "3" * 64


@pytest.mark.parametrize(
    "change",
    [
        "extra_field",
        "missing_field",
        "missing_image",
        "unchanged_false",
        "missing_suite",
        "duplicate_suite",
        "missing_log",
        "bad_hash",
        "wrong_capture_path",
        "escaping_path",
        "extra_capture_field",
        "non_object_manifest",
        "non_array_captures",
        "non_object_capture",
    ],
)
def test_partial_or_malformed_w1_binding_cannot_become_selection_evidence(change):
    record = w1_record()
    if change == "extra_field":
        record["extra"] = True
    elif change == "missing_field":
        del record["target_path_prefix"]
    elif change == "missing_image":
        record["image_id"] = None
    elif change == "unchanged_false":
        record["inputs_unchanged"] = False
    elif change == "missing_suite":
        record["captures"].pop()
    elif change == "duplicate_suite":
        record["captures"][-1] = record["captures"][0]
    elif change == "missing_log":
        del record["captures"][0]["artifact_hashes"]["stdout.txt"]
    elif change == "bad_hash":
        record["exported_files"]["src/crawler.py"] = "invalid"
    elif change == "wrong_capture_path":
        record["captures"][0]["artifact_id"] = "test-captures/baseline/capture.json"
    elif change == "escaping_path":
        record["exported_files"] = {"../secret": "b" * 64}
    elif change == "extra_capture_field":
        record["captures"][0]["trusted"] = True
    elif change == "non_object_manifest":
        record["exported_files"] = []
    elif change == "non_array_captures":
        record["captures"] = {}
    else:
        record["captures"][0] = "unexpected"
    with pytest.raises(ValueError):
        BaselineBinding.from_w1(record)


def test_normalized_binding_rejects_duplicate_manifest_paths():
    payload = BaselineBinding.from_w1(w1_record()).model_dump(mode="json")
    payload["exported_files"] *= 2
    with pytest.raises(ValidationError, match="unique"):
        BaselineBinding.model_validate(payload)
