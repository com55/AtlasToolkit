from atlas_toolkit.domain.effective_model import derive_effective_model


def _pristine():
    return {
        "arm": {"atlasName": "arm", "offsets": [1, 2, 10, 10], "index": -1, "split": None, "pad": None, "extraPairs": []},
        "leg": {"atlasName": "leg", "offsets": None, "index": -1, "split": None, "pad": None, "extraPairs": []},
    }


def test_no_batches_passes_pristine_regions_through_unchanged():
    m = derive_effective_model(_pristine(), [])
    assert m["region_names"] == ["arm", "leg"]
    assert m["regions"]["arm"] == _pristine()["arm"]
    assert m["labels"] == {}
    assert m["modified_keys"] == set()


def test_mod_batch_names_go_into_modified_keys_regions_labels_untouched():
    m = derive_effective_model(_pristine(), [{"type": "mod", "names": ["arm"], "source": "src"}])
    assert m["modified_keys"] == {"arm"}
    assert m["labels"] == {}
    assert m["region_names"] == ["arm", "leg"]


def test_add_batch_new_key_appended_gets_defaults_in_labels_and_modified():
    add = {"type": "add", "internal_key": "helmet", "atlas_name": "helmet", "source_canvas": "fake-canvas"}
    m = derive_effective_model(_pristine(), [add])
    assert m["region_names"] == ["arm", "leg", "helmet"]
    assert m["regions"]["helmet"] == {
        "atlasName": "helmet", "offsets": None, "index": -1, "split": None, "pad": None, "extraPairs": [],
    }
    assert m["labels"]["helmet"] == "helmet"
    assert "helmet" in m["modified_keys"]
    assert "arm" not in m["labels"]  # untouched pristine key stays absent from labels


def test_remove_batch_key_drops_out_of_names_and_regions_entirely():
    m = derive_effective_model(_pristine(), [{"type": "remove", "target_key": "leg"}])
    assert m["region_names"] == ["arm"]
    assert "leg" not in m["regions"]
    assert "leg" not in m["modified_keys"]


def test_rename_batch_atlas_name_overridden_order_unchanged():
    original = _pristine()
    m = derive_effective_model(original, [{"type": "rename", "target_key": "arm", "new_atlas_name": "forearm"}])
    assert m["region_names"] == ["arm", "leg"]  # key never changes
    assert m["regions"]["arm"]["atlasName"] == "forearm"
    assert m["regions"]["arm"]["offsets"] == original["arm"]["offsets"]  # rest of region-meta untouched
    assert m["labels"]["arm"] == "forearm"
    assert "arm" in m["modified_keys"]


def test_add_then_rename_the_same_new_region_chained_by_internal_key():
    add = {"type": "add", "internal_key": "helmet", "atlas_name": "helmet", "source_canvas": "fake-canvas"}
    ren = {"type": "rename", "target_key": "helmet", "new_atlas_name": "iron_helmet"}
    m = derive_effective_model(_pristine(), [add, ren])
    assert m["region_names"] == ["arm", "leg", "helmet"]
    assert m["regions"]["helmet"]["atlasName"] == "iron_helmet"
    assert m["labels"]["helmet"] == "iron_helmet"


def test_add_then_remove_the_same_new_region_never_appears_in_output():
    add = {"type": "add", "internal_key": "helmet", "atlas_name": "helmet", "source_canvas": "fake-canvas"}
    rem = {"type": "remove", "target_key": "helmet"}
    m = derive_effective_model(_pristine(), [add, rem])
    assert m["region_names"] == ["arm", "leg"]
    assert "helmet" not in m["regions"]


def test_is_shape_agnostic_extra_unrelated_fields_pass_through():
    scaled_like = {
        "arm": {"atlasName": "arm", "offsets": None, "index": -1, "split": None, "pad": None, "extraPairs": [], "bounds": [0, 0, 5, 5]},
    }
    m = derive_effective_model(scaled_like, [{"type": "rename", "target_key": "arm", "new_atlas_name": "forearm"}])
    assert m["regions"]["arm"]["atlasName"] == "forearm"
    assert m["regions"]["arm"]["bounds"] == [0, 0, 5, 5]  # passed through untouched, never read/required
