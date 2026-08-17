import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from api.frontend import CanonicalJsonVfInfoAdapter, VfInfoValidationError
from api.frontend.json_adapter import _canonical_schema_validator
from api.input_api import InputAPI


class CanonicalJsonVfInfoAdapterTest(unittest.TestCase):
    def setUp(self):
        self.fixtures = Path(__file__).parent / "fixtures/canonical_vf_info"

    def test_input_api_loads_and_validates_canonical_json(self):
        vf_info = InputAPI.load_canonical_json(
            self.fixtures / "v1_valid_loop.json"
        )

        self.assertEqual(vf_info.schema_version, 1)
        self.assertEqual(vf_info.context[0].loop_id, "loop.row")

    def test_semantically_invalid_payload_exposes_validator_diagnostics(self):
        with self.assertRaises(VfInfoValidationError) as raised:
            CanonicalJsonVfInfoAdapter.load(
                self.fixtures / "v1_invalid_loop_scope.json"
            )

        self.assertEqual(
            {item.code for item in raised.exception.diagnostics},
            {"loop_back_edge_out_of_scope"},
        )

    def test_missing_required_fields_expose_schema_diagnostic(self):
        with self.assertRaises(VfInfoValidationError) as raised:
            CanonicalJsonVfInfoAdapter.from_payload({"schema_version": 1})

        self.assertEqual(
            {item.code for item in raised.exception.diagnostics},
            {"canonical_json_schema_error"},
        )

    def test_invalid_json_exposes_source_location(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.json"
            path.write_text('{"schema_version": 1,', encoding="utf-8")

            with self.assertRaises(VfInfoValidationError) as raised:
                CanonicalJsonVfInfoAdapter.load(path)

        diagnostic = raised.exception.diagnostics[0]
        self.assertEqual(diagnostic.code, "canonical_json_syntax_error")
        self.assertEqual(diagnostic.location.line, 1)
        self.assertEqual(diagnostic.location.source, str(path))

    def test_root_array_is_rejected_without_legacy_inference(self):
        with self.assertRaises(VfInfoValidationError) as raised:
            CanonicalJsonVfInfoAdapter.from_payload([])  # type: ignore[arg-type]

        self.assertEqual(
            raised.exception.diagnostics[0].code,
            "canonical_payload_decode_error",
        )

    def _valid_payload(self):
        path = self.fixtures / "v1_valid_loop.json"
        return json.loads(path.read_text(encoding="utf-8"))

    def test_unknown_top_level_field_is_rejected_by_shared_schema(self):
        payload = self._valid_payload()
        payload["unexpected"] = True

        with self.assertRaises(VfInfoValidationError) as raised:
            CanonicalJsonVfInfoAdapter.from_payload(payload)

        diagnostic = raised.exception.diagnostics[0]
        self.assertEqual(diagnostic.code, "canonical_json_schema_error")
        self.assertEqual(diagnostic.location.path, "$")
        self.assertEqual(diagnostic.context["validator"], "additionalProperties")

    def test_unknown_instruction_field_is_rejected_by_shared_schema(self):
        payload = self._valid_payload()
        instruction = payload["context"][0]["body"][0]
        instruction["unexpected"] = True

        with self.assertRaises(VfInfoValidationError) as raised:
            CanonicalJsonVfInfoAdapter.from_payload(payload)

        self.assertEqual(
            {item.code for item in raised.exception.diagnostics},
            {"canonical_json_schema_error"},
        )

    def test_misspelled_dependencies_field_is_not_silently_dropped(self):
        payload = self._valid_payload()
        instruction = payload["context"][0]["body"][0]
        instruction["dependecies"] = instruction.pop("dependencies")

        with self.assertRaises(VfInfoValidationError) as raised:
            CanonicalJsonVfInfoAdapter.from_payload(payload)

        self.assertEqual(
            {item.code for item in raised.exception.diagnostics},
            {"canonical_json_schema_error"},
        )

    def test_missing_jsonschema_only_disables_canonical_json_loading(self):
        missing = ModuleNotFoundError("No module named 'jsonschema'")
        missing.name = "jsonschema"
        _canonical_schema_validator.cache_clear()
        try:
            with mock.patch(
                "api.frontend.json_adapter.import_module",
                side_effect=missing,
            ):
                builder = InputAPI.new_vf_info_builder()
                self.assertIsNotNone(builder)
                with self.assertRaisesRegex(
                    RuntimeError,
                    "optional 'jsonschema' package",
                ):
                    CanonicalJsonVfInfoAdapter.from_payload(self._valid_payload())
        finally:
            _canonical_schema_validator.cache_clear()


if __name__ == "__main__":
    unittest.main()
