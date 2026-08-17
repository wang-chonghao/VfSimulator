import unittest
import json
import re
import tempfile
from pathlib import Path

from api.input_api import InputAPI
from api.cce_adapter import parse_cce_vf_info
from api.input_symbols import (
    normalize_dtype,
    normalize_form,
    normalize_membar_type,
    normalize_opcode,
    specialize_opcode,
)
from api.simulator_costmodel import CoreVfCostModel
from api.vf_info import Membar, ValueInfo, VFInfo, VFInst, VFLoop, canonicalize_vf_info
from api.vf_lowering import VFInfoLowerer


ROOT = Path(__file__).resolve().parents[1]


class VfInfoApiTest(unittest.TestCase):
    def test_input_symbols_normalize_public_aliases(self):
        self.assertEqual(normalize_dtype("float32"), "fp32")
        self.assertEqual(normalize_dtype("fp64"), "fp64")
        self.assertEqual(normalize_form("fp32_to_s32"), "f32_to_s32")
        self.assertEqual(normalize_form("f16_to_s32"), "f16_to_s32")
        self.assertEqual(normalize_opcode("vld"), "VLDS")
        self.assertEqual(normalize_opcode("vpack"), "VPACK")
        self.assertEqual(normalize_opcode("vsstb"), "VSSTB")
        self.assertEqual(normalize_opcode("vunknown"), "VUNKNOWN")
        self.assertEqual(specialize_opcode("vcvt", "fp32_to_s32"), "VCVT_F32_TO_S32")
        self.assertEqual(specialize_opcode("vcvt", "f32_to_bf16"), "VCVT_F32_TO_BF16")
        self.assertEqual(specialize_opcode("vcvt", "f16_to_s32"), "VCVT")
        self.assertEqual(normalize_membar_type("SMEM_BAR.VLD_VST"), "VLD_VST")
        self.assertEqual(normalize_membar_type("SMEM_BAR.VV_ALL"), "VV_ALL")

    def test_canonicalize_vf_info_normalizes_external_symbols(self):
        vf_info = VFInfo(
            default_dtype="float32",
            values={
                "a": ValueInfo("a", "ub", "float32", [64]),
                "b": ValueInfo("b", "reg", "f32", [64]),
                "c": ValueInfo("c", "register", "s32", [64]),
            },
            context=[
                VFLoop(
                    1,
                    body=[
                        VFInst("vld", ["a"], ["b"], "float32"),
                        VFInst("vcvt", ["b"], ["c"]),
                        Membar("MEMBAR.VST_VLD"),
                    ],
                )
            ],
        )

        canonical = canonicalize_vf_info(vf_info)
        body = canonical.context[0].body
        self.assertEqual(canonical.default_dtype, "fp32")
        self.assertEqual(canonical.values["a"].storage, "UB")
        self.assertEqual(canonical.values["b"].storage, "Register")
        self.assertEqual(canonical.values["c"].dtype, "int32")
        self.assertEqual(body[0].name, "VLDS")
        self.assertEqual(body[0].form, "fp32")
        self.assertEqual(body[1].name, "VCVT_F32_TO_S32")
        self.assertEqual(body[1].form, "f32_to_s32")
        self.assertEqual(body[2].type, "VST_VLD")

    def test_canonicalize_keeps_uncovered_symbols_for_core_fallback(self):
        canonical = canonicalize_vf_info(
            VFInfo(
                default_dtype="fp64",
                values={
                    "a": ValueInfo("a", "Register", "fp16", [64]),
                    "b": ValueInfo("b", "Register", "int32", [64]),
                },
                context=[
                    VFInst("vcvt", ["a"], ["b"]),
                    VFInst("vunknown", ["a"], ["b"], "fp64"),
                    Membar("SMEM_BAR.VV_ALL"),
                ],
            )
        )
        body = canonical.context
        self.assertEqual(body[0].name, "VCVT")
        self.assertEqual(body[0].form, "f16_to_s32")
        self.assertEqual(body[1].name, "VUNKNOWN")
        self.assertEqual(body[1].form, "fp64")
        self.assertEqual(body[2].type, "VV_ALL")

    def test_legacy_core_payload_still_uses_paramdb_fallback(self):
        payload = {
            "dtype": "fp32",
            "values": {
                "memA": {"value_id": "memA", "storage": "UB", "dtype": "fp32", "shape": [64]},
                "v0": {"value_id": "v0", "storage": "Register", "dtype": "fp32", "shape": [64]},
                "v1": {"value_id": "v1", "storage": "Register", "dtype": "fp32", "shape": [64]},
            },
            "program": [
                {
                    "type": "loop",
                    "iters": 1,
                    "body": [
                        {"type": "inst", "op": "VLDS", "form": "fp32", "src": ["memA"], "dst": ["v0"]},
                        {"type": "inst", "op": "VUNKNOWN", "form": "fp32", "src": ["v0"], "dst": ["v1"]},
                    ],
                }
            ],
        }
        result = CoreVfCostModel(
            base_dir=ROOT,
            out_dir="/tmp/vfsim-vfinfo-legacy-fallback",
        )._run_lowered_payload(payload)
        self.assertGreater(result["vf_end_cycle"], 0)

    def test_json_adapter_allows_legacy_symbols_for_core_fallback(self):
        payload = {
            "dtype": "fp32",
            "values": {
                "a": {"value_id": "a", "storage": "Register", "dtype": "fp32", "shape": [64]},
                "b": {"value_id": "b", "storage": "Register", "dtype": "fp32", "shape": [64]},
            },
            "program": [
                {"type": "inst", "op": "VUNKNOWN", "form": "fp32", "src": ["a"], "dst": ["b"]}
            ],
        }
        from api.json_adapter import JsonVfInfoAdapter

        vf_info = JsonVfInfoAdapter.from_payload(payload)
        self.assertEqual(vf_info.context[0].name, "VUNKNOWN")

    def test_run_payload_writes_model_warnings_for_fallback(self):
        payload = {
            "dtype": "fp32",
            "values": {
                "a": {"value_id": "a", "storage": "Register", "dtype": "fp32", "shape": [64]},
                "b": {"value_id": "b", "storage": "Register", "dtype": "fp32", "shape": [64]},
            },
            "program": [
                {
                    "type": "loop",
                    "iters": 1,
                    "body": [
                        {"type": "inst", "op": "VUNKNOWN", "form": "fp32", "src": ["a"], "dst": ["b"]}
                    ],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            out_dir = Path(tmpdir) / "out"
            CoreVfCostModel(base_dir=ROOT, out_dir=out_dir).run_payload(payload)
            warnings = json.loads((out_dir / "model_warnings.json").read_text(encoding="utf-8"))
        kinds = {item["kind"] for item in warnings["instruction_fallback_warnings"]}
        self.assertIn("unsupported_isa_op", kinds)

    def test_json_and_cce_adapters_return_mixed_dtype_vf_info(self):
        json_info = InputAPI.load_json_trace(ROOT / "VFtest/tadd_tcvt_tadd.json")
        cce_info = InputAPI.load_cce_file(ROOT / "cce_code/tadd_tcvt_tadd.dsl")

        for vf_info in (json_info, cce_info):
            forms = [inst.form for inst in vf_info.context[0].body]
            self.assertIn("f32_to_f16", forms)
            self.assertIn("fp16", forms)
            self.assertEqual(vf_info.values[next(
                value_id
                for value_id, value in vf_info.values.items()
                if value.dtype == "fp16" and value.storage == "Register"
            )].dtype, "fp16")

    def test_cce_adapter_parses_vpack_and_vsstb_cast_operands(self):
        source = """
        void softmax_vf(__ubuf__ half *nz_buffer_Ptr) {
          __VEC_SCOPE__ {
            vector_f16 vreg_x_exp_even_f16;
            vector_bool preg_low_half = pset_b16(PAT_ALL);
            vpack((vector_u16 &)vreg_x_exp_even_f16, (vector_u32 &)vreg_x_exp_even_f16, LOWER);
            vsstb(vreg_x_exp_even_f16, ((__ubuf__ half *&)nz_buffer_Ptr), VSSTB_CONFIG, preg_low_half, POST_UPDATE);
          }
        }
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "softmax.dsl"
            path.write_text(source, encoding="utf-8")
            vf_info = InputAPI.load_cce_file(path, kernel_name="softmax_vf")

        insts = vf_info.context
        self.assertEqual(insts[0].name, "VPACK")
        self.assertEqual(insts[0].src, ["vreg_x_exp_even_f16"])
        self.assertEqual(insts[0].dst, ["vreg_x_exp_even_f16"])
        self.assertEqual(insts[0].form, "b32")
        self.assertEqual(insts[1].name, "VSSTB")
        self.assertEqual(insts[1].src, ["vreg_x_exp_even_f16"])
        self.assertEqual(insts[1].dst, ["nz_buffer_Ptr"])
        self.assertEqual(insts[1].form, "b16")
        self.assertEqual(vf_info.values["vreg_x_exp_even_f16"].dtype, "fp16")
        self.assertEqual(vf_info.values["nz_buffer_Ptr"].storage, "UB")

    def test_cce_adapter_parses_symbolic_division_loop_bound(self):
        source = """
        void loop_bound_vf(__ubuf__ float *a) {
          constexpr uint16_t kRows = 128;
          __VEC_SCOPE__ {
            for (uint16_t i = 0; i < kRows / 4; ++i) {
              vector_f32 v0;
              vlds(v0, a, 0, NORM);
            }
          }
        }
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "loop_bound.dsl"
            path.write_text(source, encoding="utf-8")
            vf_info = parse_cce_vf_info(
                path,
                kernel_name="loop_bound_vf",
                loop_params={"kRows": 128},
            )

        self.assertEqual(vf_info.context[0].count, 32)

    def test_cce_adapter_parses_vmulscvt_conversion_form(self):
        source = """
        void vmulscvt_vf() {
          __VEC_SCOPE__ {
            vector_f32 expv;
            vector_f16 cvt;
            vmulscvt(cvt, expv, 1.0f, pset_b32(PAT_ALL), PART_EVEN);
          }
        }
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "vmulscvt.dsl"
            path.write_text(source, encoding="utf-8")
            vf_info = InputAPI.load_cce_file(path, kernel_name="vmulscvt_vf")

        self.assertEqual(vf_info.context[0].name, "VMULSCVT")
        self.assertEqual(vf_info.context[0].form, "f32_to_f16")

    def test_cce_adapter_registers_first_vector_decl_inside_loop(self):
        source = """
        void loop_decl_vf(__ubuf__ float *src) {
          __VEC_SCOPE__ {
            vector_bool pred = pset_b32(PAT_ALL);
            for (uint16_t row = 0; row < 2; ++row) {
              vector_f32 x0;
              vector_f32 exp0;
              vld(x0, src, 0, NORM);
              vmuls(x0, x0, 0.125f, pred);
              vexpdif(exp0, x0, x0, pred, PART_ODD);
            }
          }
        }
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "loop_decl.dsl"
            path.write_text(source, encoding="utf-8")
            vf_info = InputAPI.load_cce_file(path, kernel_name="loop_decl_vf")

        loop = vf_info.context[0]
        vmuls = next(inst for inst in loop.body if getattr(inst, "name", None) == "VMULS")
        vexpdif = next(inst for inst in loop.body if getattr(inst, "name", None) == "VEXPDIF")
        self.assertEqual(vmuls.src, ["x0"])
        self.assertEqual(vmuls.dst, ["x0"])
        self.assertEqual(vexpdif.src, ["x0", "x0"])
        self.assertEqual(vexpdif.dst, ["exp0"])

    def test_cce_catalog_binder_rejects_missing_or_misordered_operands(self):
        sources = {
            "missing_binary_source": """
                void bad(__ubuf__ float *a) {
                  __VEC_SCOPE__ {
                    vector_f32 dst, lhs;
                    vadd(dst, lhs);
                  }
                }
            """,
            "ub_used_as_register": """
                void bad(__ubuf__ float *a) {
                  __VEC_SCOPE__ {
                    vector_f32 dst, rhs;
                    vadd(dst, a, rhs, pset_b32(PAT_ALL));
                  }
                }
            """,
            "predicate_used_as_scalar": """
                void bad(__ubuf__ float *a) {
                  __VEC_SCOPE__ {
                    vector_f32 dst, src;
                    vadds(dst, src, pset_b32(PAT_ALL), 1.0f);
                  }
                }
            """,
        }
        for name, source in sources.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmpdir:
                path = Path(tmpdir) / "bad.dsl"
                path.write_text(source, encoding="utf-8")
                with self.assertRaises(ValueError):
                    parse_cce_vf_info(path, kernel_name="bad")

    def test_cce_catalog_rejects_invalid_forms(self):
        sources = {
            "vexpdif_fp16": """
                void bad() {
                  __VEC_SCOPE__ {
                    vector_f16 dst, lhs, rhs;
                    vector_bool mask = pset_b16(PAT_ALL);
                    vexpdif(dst, lhs, rhs, mask, PART_EVEN);
                  }
                }
            """,
            "vcvt_same_dtype": """
                void bad() {
                  __VEC_SCOPE__ {
                    vector_f16 dst, src;
                    vector_bool mask = pset_b16(PAT_ALL);
                    vcvt(dst, src, mask, ROUND_R, RS_DISABLE, PART_EVEN);
                  }
                }
            """,
            "vmulscvt_same_dtype": """
                void bad() {
                  __VEC_SCOPE__ {
                    vector_f32 dst, src;
                    vector_bool mask = pset_b32(PAT_ALL);
                    vmulscvt(dst, src, 1.0f, mask, PART_EVEN);
                  }
                }
            """,
        }
        for name, source in sources.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmpdir:
                path = Path(tmpdir) / "bad_form.dsl"
                path.write_text(source, encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "Unsupported semantic form"):
                    parse_cce_vf_info(path, kernel_name="bad")

    def test_cce_catalog_rejects_extra_or_unresolved_arguments(self):
        sources = {
            "extra_compute_argument": """
                void bad() {
                  __VEC_SCOPE__ {
                    vector_f32 dst, lhs, rhs;
                    vector_bool mask = pset_b32(PAT_ALL);
                    vadd(dst, lhs, rhs, mask, TOTALLY_INVALID);
                  }
                }
            """,
            "invalid_load_config": """
                void bad(__ubuf__ float *a) {
                  __VEC_SCOPE__ {
                    vector_f32 dst;
                    vld(dst, a, nonsense, also_nonsense);
                  }
                }
            """,
            "unsupported_load_mode": """
                void bad(__ubuf__ float *a) {
                  __VEC_SCOPE__ {
                    vector_f32 dst;
                    vld(dst, a, 0, TOTALLY_INVALID);
                  }
                }
            """,
            "undeclared_predicate": """
                void bad() {
                  __VEC_SCOPE__ {
                    vector_f32 dst, lhs, rhs;
                    vadd(dst, lhs, rhs, potato);
                  }
                }
            """,
            "predicate_name_contains_pset": """
                void bad() {
                  __VEC_SCOPE__ {
                    vector_f32 dst, lhs, rhs;
                    vadd(dst, lhs, rhs, not_a_pset_b32_value);
                  }
                }
            """,
        }
        for name, source in sources.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmpdir:
                path = Path(tmpdir) / "bad_argument.dsl"
                path.write_text(source, encoding="utf-8")
                with self.assertRaises(ValueError):
                    parse_cce_vf_info(path, kernel_name="bad")

    def test_cce_predicate_uses_declared_bool_not_name_prefix(self):
        source = """
            void valid() {
              vector_bool lane_mask = pset_b32(PAT_ALL);
              __VEC_SCOPE__ {
                vector_f32 dst, lhs, rhs;
                vadd(dst, lhs, rhs, lane_mask);
              }
            }
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "predicate.dsl"
            path.write_text(source, encoding="utf-8")
            vf_info = parse_cce_vf_info(path, kernel_name="valid")
        self.assertEqual(vf_info.context[0].name, "VADD")

    def test_cce_predicate_obeys_lexical_scope_and_declaration_order(self):
        sources = {
            "use_before_declaration": """
                void bad() {
                  __VEC_SCOPE__ {
                    vector_f32 dst, lhs, rhs;
                    vadd(dst, lhs, rhs, late_mask);
                    vector_bool late_mask = pset_b32(PAT_ALL);
                  }
                }
            """,
            "loop_local_used_after_loop": """
                void bad() {
                  __VEC_SCOPE__ {
                    vector_f32 dst, lhs, rhs;
                    for (int i = 0; i < 2; ++i) {
                      vector_bool local_mask = pset_b32(PAT_ALL);
                      vadd(dst, lhs, rhs, local_mask);
                    }
                    vadd(dst, lhs, rhs, local_mask);
                  }
                }
            """,
        }
        for name, source in sources.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmpdir:
                path = Path(tmpdir) / "bad_scope.dsl"
                path.write_text(source, encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "must be a predicate"):
                    parse_cce_vf_info(path, kernel_name="bad")

    def test_cce_load_store_and_vdup_call_variants(self):
        source = """
            void valid(__ubuf__ float *a, __ubuf__ float *b) {
              __VEC_SCOPE__ {
                vector_f32 value, broadcast;
                vector_bool mask = pset_b32(PAT_ALL);
                vlds(value, a, 64, NORM, POST_UPDATE);
                vsts(value, b, 64, NORM_B32, mask, POST_UPDATE);
                vdup(value, 0.0f, mask, MODE_ZEROING);
                vdup(broadcast, value, mask, POS_LOWEST, MODE_ZEROING);
              }
            }
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "call_variants.dsl"
            path.write_text(source, encoding="utf-8")
            vf_info = parse_cce_vf_info(path, kernel_name="valid")
        self.assertEqual(
            [node.name for node in vf_info.context],
            ["VLDS", "VSTS", "VDUP", "VDUP"],
        )

    def test_cce_timing_optional_store_forms_are_semantically_accepted(self):
        source = """
            void valid(__ubuf__ float *a) {
              __VEC_SCOPE__ {
                vector_f32 value;
                vector_bool mask = pset_b32(PAT_ALL);
                vstus(value, a, 0, NORM_B32, mask);
                vstas(value, a, 64, NORM_B32, mask, POST_UPDATE);
              }
            }
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "optional_stores.dsl"
            path.write_text(source, encoding="utf-8")
            vf_info = parse_cce_vf_info(path, kernel_name="valid")
        self.assertEqual(
            [(node.name, node.form) for node in vf_info.context],
            [("VSTUS", "fp32"), ("VSTAS", "fp32")],
        )

    def test_cce_rejects_invalid_vdup_call_variant_combinations(self):
        sources = (
            "vdup(dst, src, mask, POS_LOWEST);",
            "vdup(dst, src, mask, MODE_ZEROING, MODE_ZEROING);",
        )
        for call in sources:
            with self.subTest(call=call), tempfile.TemporaryDirectory() as tmpdir:
                path = Path(tmpdir) / "bad_vdup.dsl"
                path.write_text(
                    f"""
                    void bad() {{
                      __VEC_SCOPE__ {{
                        vector_f32 dst, src;
                        vector_bool mask = pset_b32(PAT_ALL);
                        {call}
                      }}
                    }}
                    """,
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(ValueError, "call variant"):
                    parse_cce_vf_info(path, kernel_name="bad")

    def test_cce_offset_rejects_non_affine_products(self):
        for expression in ("i * j", "i * i"):
            with self.subTest(expression=expression), tempfile.TemporaryDirectory() as tmpdir:
                path = Path(tmpdir) / "non_affine.dsl"
                path.write_text(
                    f"""
                    void bad(__ubuf__ float *a) {{
                      __VEC_SCOPE__ {{
                        vector_f32 value;
                        for (int i = 0; i < 2; ++i) {{
                          for (int j = 0; j < 2; ++j) {{
                            vlds(value, a, {expression}, NORM);
                          }}
                        }}
                      }}
                    }}
                    """,
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(ValueError, "invalid offset expression"):
                    parse_cce_vf_info(path, kernel_name="bad")

    def test_cce_local_integer_affine_offsets(self):
        source = """
            void valid(__ubuf__ float *input) {
              const uint32_t base = 1;
              __VEC_SCOPE__ {
                vector_f32 value;
                for (int i = 0; i < 2; ++i) {
                  uint32_t stride = 64;
                  uint32_t off = stride * i + base;
                  vlds(value, input, off, NORM);
                }
              }
            }
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "local_affine.dsl"
            path.write_text(source, encoding="utf-8")
            vf_info = parse_cce_vf_info(path, kernel_name="valid")
        self.assertEqual(vf_info.context[0].body[0].name, "VLDS")

    def test_cce_local_scalar_offsets_obey_scope_and_affine_rules(self):
        sources = {
            "use_before_declaration": (
                "invalid offset expression",
                """
                void bad(__ubuf__ float *input) {
                  __VEC_SCOPE__ {
                    vector_f32 value;
                    vlds(value, input, off, NORM);
                    uint32_t off = 64;
                  }
                }
                """,
            ),
            "loop_local_used_outside": (
                "invalid offset expression",
                """
                void bad(__ubuf__ float *input) {
                  __VEC_SCOPE__ {
                    vector_f32 value;
                    for (int i = 0; i < 2; ++i) {
                      uint32_t off = 64 * i;
                      vlds(value, input, off, NORM);
                    }
                    vlds(value, input, off, NORM);
                  }
                }
                """,
            ),
            "non_affine_initializer": (
                "invalid offset expression",
                """
                void bad(__ubuf__ float *input) {
                  __VEC_SCOPE__ {
                    vector_f32 value;
                    for (int i = 0; i < 2; ++i) {
                      uint32_t off = i * i;
                      vlds(value, input, off, NORM);
                    }
                  }
                }
                """,
            ),
        }
        for name, (message, source) in sources.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmpdir:
                path = Path(tmpdir) / "bad_local_scalar.dsl"
                path.write_text(source, encoding="utf-8")
                with self.assertRaisesRegex(ValueError, message):
                    parse_cce_vf_info(path, kernel_name="bad")

    def test_cce_local_float_is_available_as_scalar_operand(self):
        source = """
            void valid() {
              __VEC_SCOPE__ {
                vector_f32 dst, src;
                vector_bool mask = pset_b32(PAT_ALL);
                float scale = 0.5f;
                vadds(dst, src, scale, mask);
              }
            }
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "local_float.dsl"
            path.write_text(source, encoding="utf-8")
            vf_info = parse_cce_vf_info(path, kernel_name="valid")
        self.assertEqual(vf_info.context[0].src, ["src", "scale"])

    def test_cce_local_scalar_initializer_is_validated_only_for_offsets(self):
        source = """
            void valid() {
              uint32_t tid = get_block_idx();
              __VEC_SCOPE__ {
                vector_f32 dst, src;
                vector_bool mask = pset_b32(PAT_ALL);
                vadds(dst, src, tid, mask);
              }
            }
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "delayed_scalar.dsl"
            path.write_text(source, encoding="utf-8")
            vf_info = parse_cce_vf_info(path, kernel_name="valid")
        self.assertEqual(vf_info.context[0].src, ["src", "tid"])

        offset_source = """
            void bad(__ubuf__ float *input) {
              uint32_t tid = get_block_idx();
              __VEC_SCOPE__ {
                vector_f32 value;
                vlds(value, input, tid, NORM);
              }
            }
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "invalid_delayed_offset.dsl"
            path.write_text(offset_source, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "invalid offset expression"):
                parse_cce_vf_info(path, kernel_name="bad")

    def test_cce_rejects_unmodeled_scope_statements_with_source_text(self):
        sources = {
            "assignment": "off = 64 * i;",
            "non_vector_call": "update_offset(off);",
        }
        for name, statement in sources.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmpdir:
                path = Path(tmpdir) / "unsupported_statement.dsl"
                path.write_text(
                    f"""
                    void bad() {{
                      __VEC_SCOPE__ {{
                        uint32_t off = 0;
                        for (int i = 0; i < 2; ++i) {{
                          {statement}
                        }}
                      }}
                    }}
                    """,
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(
                    ValueError,
                    re.escape(statement),
                ):
                    parse_cce_vf_info(path, kernel_name="bad")

    def test_cce_standalone_pset_remains_an_explicit_noop(self):
        source = """
            void valid() {
              __VEC_SCOPE__ {
                vector_f32 dst, lhs, rhs;
                vector_bool mask = pset_b32(PAT_ALL);
                pset_b32(PAT_ALL);
                vadd(dst, lhs, rhs, mask);
              }
            }
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "pset_noop.dsl"
            path.write_text(source, encoding="utf-8")
            vf_info = parse_cce_vf_info(path, kernel_name="valid")
        self.assertEqual([node.name for node in vf_info.context], ["VADD"])

    def test_cce_adapter_parses_mem_bar_call(self):
        source = """
        void barrier_vf(__ubuf__ float *a, __ubuf__ float *b) {
          __VEC_SCOPE__ {
            vector_f32 v0;
            vsts(v0, a, 0, NORM_B32, pset_b32(PAT_ALL));
            mem_bar(VST_VLD);
            vlds(v0, b, 0, NORM);
          }
        }
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "barrier.dsl"
            path.write_text(source, encoding="utf-8")
            vf_info = InputAPI.load_cce_file(path, kernel_name="barrier_vf")

        membars = [node for node in vf_info.context if isinstance(node, Membar)]
        self.assertEqual(len(membars), 1)
        self.assertEqual(membars[0].type, "VST_VLD")

    def test_cce_adapter_keeps_scalar_function_params_as_scalar(self):
        source = """
        void scalar_param_vf(__ubuf__ float *a, float epsilon) {
          __VEC_SCOPE__ {
            vector_bool p = pset_b32(PAT_ALL);
            vector_f32 v0;
            vlds(v0, a, 0, NORM);
            vadds(v0, v0, epsilon, p);
          }
        }
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "scalar_param.dsl"
            path.write_text(source, encoding="utf-8")
            vf_info = InputAPI.load_cce_file(path, kernel_name="scalar_param_vf")

        self.assertEqual(vf_info.values["a"].storage, "UB")
        self.assertEqual(vf_info.values["epsilon"].storage, "Scalar")

    def test_handwritten_vf_info_is_the_core_input(self):
        lhs_source = ValueInfo("lhs_input", "UB", "fp32", [16, 64])
        rhs_source = ValueInfo("rhs_input", "UB", "fp32", [16, 64])
        lhs = ValueInfo("lhs", "Register", "fp32", [64])
        rhs = ValueInfo("rhs", "Register", "fp32", [64])
        total = ValueInfo("total", "Register", "fp32", [64])
        output = ValueInfo("output", "UB", "fp32", [16, 64])
        vf_info = VFInfo(
            context=[
                VFLoop(
                    16,
                    body=[
                        VFInst("VLDS", [lhs_source], [lhs]),
                        VFInst("VLDS", [rhs_source], [rhs]),
                        VFInst("VADD", [lhs, rhs], [total]),
                        VFInst("VSTS", [total], [output]),
                    ],
                )
            ]
        )

        canonical = canonicalize_vf_info(vf_info)
        self.assertEqual(canonical.context[0].body[0].src, ["lhs_input"])
        self.assertEqual(canonical.context[0].body[0].form, "fp32")
        result = CoreVfCostModel(
            base_dir=ROOT,
            out_dir="/tmp/vfsim-vfinfo-python-handwritten",
        ).run_vf_info(canonical)
        self.assertGreater(result["vf_end_cycle"], 0)

    def test_mixed_dtype_python_prediction_matches_reference(self):
        vf_info = InputAPI.load_json_trace(ROOT / "VFtest/tadd_tcvt_tadd.json")
        result = CoreVfCostModel(
            base_dir=ROOT,
            out_dir="/tmp/vfsim-vfinfo-python-test",
        ).run_vf_info(vf_info)
        self.assertEqual(result["cycles_executed"], 72)
        self.assertEqual(result["vf_end_cycle"], 84)

    def test_core_payload_uses_explicit_storage_not_name_prefix(self):
        payload = {
            "dtype": "fp32",
            "values": {
                "input_a": {"value_id": "input_a", "storage": "UB", "dtype": "fp32", "shape": [1, 64]},
                "input_b": {"value_id": "input_b", "storage": "UB", "dtype": "fp32", "shape": [1, 64]},
                "lhs": {"value_id": "lhs", "storage": "Register", "dtype": "fp32", "shape": [64]},
                "rhs": {"value_id": "rhs", "storage": "Register", "dtype": "fp32", "shape": [64]},
                "sum": {"value_id": "sum", "storage": "Register", "dtype": "fp32", "shape": [64]},
                "output": {"value_id": "output", "storage": "UB", "dtype": "fp32", "shape": [1, 64]},
            },
            "program": [
                {
                    "type": "loop",
                    "iters": 1,
                    "body": [
                        {"type": "inst", "op": "VLDS", "form": "fp32", "src": ["input_a"], "dst": ["lhs"]},
                        {"type": "inst", "op": "VLDS", "form": "fp32", "src": ["input_b"], "dst": ["rhs"]},
                        {"type": "inst", "op": "VADD", "form": "fp32", "src": ["lhs", "rhs"], "dst": ["sum"]},
                        {"type": "inst", "op": "VSTS", "form": "fp32", "src": ["sum"], "dst": ["output"]},
                    ],
                }
            ],
        }

        result = CoreVfCostModel(
            base_dir=ROOT,
            out_dir="/tmp/vfsim-vfinfo-explicit-storage-test",
        )._run_lowered_payload(payload)
        self.assertGreater(result["vf_end_cycle"], 0)

    def test_lowering_preserves_core_symbols_with_semantic_prefixes(self):
        vf_info = InputAPI.load_json_trace(
            ROOT
            / "regression_suite/inputs/json/vadd_fusion_singlev1_tests/I128"
            / "VADD_singleV1_fusion_128loops_4vadds.json"
        )
        lowered = VFInfoLowerer().lower(vf_info)
        first_store = lowered["program"][0]["body"][-1]
        second_load = lowered["program"][1]["body"][0]

        self.assertEqual(first_store["dst"], ["mem_inter_0"])
        self.assertEqual(second_load["src"], ["mem_inter_0"])
        self.assertEqual(first_store["src"], ["V1"])


if __name__ == "__main__":
    unittest.main()
