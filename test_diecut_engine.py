import unittest

from diecut_engine import (
    build_airplane_box,
    build_insertion_box,
    estimate_sheet_utilization,
    geometry_to_dxf_bytes,
    geometry_to_json,
    geometry_to_pdf_bytes,
    geometry_to_svg,
    validate_geometry,
)


class DieCutEngineTests(unittest.TestCase):
    def test_geometry_contract_and_symmetry(self):
        geometry = build_airplane_box(200, 150, 60, 3, corner_radius=8)
        self.assertEqual(validate_geometry(geometry), [])
        contract = geometry_to_json(geometry)
        self.assertEqual(contract["schema_version"], "1.0")
        self.assertEqual(contract["derived"]["wall_height"], 60)  # 壁高 = 盒内高 H，厚度在盒外
        self.assertEqual(contract["derived"]["tab_depth"], 60)
        self.assertEqual(len(contract["panels"]), 19)  # 5 主面板 + 14 翼/侧壁
        self.assertEqual(len(contract["fold_sequence"]), 4)

        slots = [
            segment for segment in geometry.segments
            if segment.kind == "cut"
            and len(segment.points) == 5
            and segment.points[0] == segment.points[-1]
        ]
        self.assertEqual(len(slots), 2)
        self.assertAlmostEqual(
            slots[0].points[1][0] - slots[0].points[0][0],
            geometry.thickness,
        )

    def test_exports_and_nesting(self):
        geometry = build_airplane_box(200, 150, 60, 3)
        nesting = estimate_sheet_utilization(geometry, 1200, 800)
        self.assertGreaterEqual(nesting["count"], 0)
        self.assertEqual(len(nesting["candidates"]), 2)
        self.assertTrue(geometry_to_svg(geometry).startswith("<svg"))
        self.assertTrue(geometry_to_pdf_bytes(geometry).startswith(b"%PDF"))
        self.assertIn(b"SECTION", geometry_to_dxf_bytes(geometry))

    def test_insertion_box_contract_and_exports(self):
        geometry = build_insertion_box(45, 45, 104, 0.5)
        self.assertEqual(validate_geometry(geometry), [])
        kinds = {s.kind for s in geometry.segments}
        self.assertIn("cut", kinds)
        self.assertIn("crease", kinds)
        self.assertTrue(geometry_to_svg(geometry).startswith("<svg"))
        self.assertTrue(geometry_to_pdf_bytes(geometry).startswith(b"%PDF"))
        self.assertIn(b"SECTION", geometry_to_dxf_bytes(geometry))


if __name__ == "__main__":
    unittest.main()
