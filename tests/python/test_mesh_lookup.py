from atlas_toolkit.mesh.lookup import build_mesh_lookup


def test_keeps_only_mesh_type_attachments_drops_others():
    attachments = {
        "SAMPLE_1": {"type": "Mesh", "path": "SAMPLE_1", "uvs": [0, 0, 1, 0, 0, 1], "triangles": [0, 1, 2]},
        "Halo": {"type": "Region", "path": "Halo"},
        "fronthair": {"type": "LinkedMesh", "path": "fronthair"},
    }
    lookup = build_mesh_lookup({"attachments": attachments})
    assert list(lookup.keys()) == ["SAMPLE_1"]
    assert lookup["SAMPLE_1"] == {"uvs": [0, 0, 1, 0, 0, 1], "triangles": [0, 1, 2]}


def test_returns_empty_dict_when_no_attachments_are_mesh_type():
    attachments = {"Halo": {"type": "Region", "path": "Halo"}}
    lookup = build_mesh_lookup({"attachments": attachments})
    assert lookup == {}
