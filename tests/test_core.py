"""Behaviour tests for the pure core layer: models, errors, paths, JSON."""

import dataclasses
import copy
import unittest
from collections.abc import Mapping

from src.core import (
    AlreadyExists,
    Conflict,
    ContentError,
    ContentScope,
    DeleteSnapshot,
    DirectoryNotEmpty,
    DocumentSnapshot,
    InvalidArgument,
    InvalidMove,
    InvalidName,
    MigrationError,
    NodeSnapshot,
    NotDirectory,
    NotDocument,
    NotFound,
    ParsedPath,
    PathOutsideRoot,
    ProtectedNode,
    StorageBusy,
    TreeItem,
    UnsupportedSchema,
    freeze_json,
    json_equal,
    parse_path,
    thaw_json,
    validate_metadata,
    validate_name,
)


class TestNameRules(unittest.TestCase):
    def test_allows_chinese_and_spaces(self):
        for name in ("文档", "设定 集", "a", "1", "-", "a.b", "名前.txt"):
            with self.subTest(name=name):
                self.assertIsNone(validate_name(name))

    def test_rejects_empty_dot_dotdot_and_separators(self):
        for name in ("", ".", "..", "a/b", "a\\b", "a\x00b"):
            with self.subTest(name=name):
                with self.assertRaises(InvalidName) as ctx:
                    validate_name(name)
                self.assertEqual(ctx.exception.code, "invalid_name")

    def test_rejects_non_string(self):
        with self.assertRaises(InvalidName):
            validate_name(None)  # type: ignore[arg-type]


class TestParsePath(unittest.TestCase):
    def test_parse_keeps_dotdot(self):
        self.assertEqual(parse_path('文档/../设定').parts, ('文档', '..', '设定'))
        self.assertFalse(json_equal(True, 1))

    def test_absolute_root(self):
        parsed = parse_path("/")
        self.assertEqual(
            parsed,
            ParsedPath(absolute=True, parts=(), trailing_slash=True),
        )

    def test_tilde_root(self):
        self.assertEqual(
            parse_path("~"),
            ParsedPath(absolute=True, parts=(), trailing_slash=False),
        )
        self.assertEqual(
            parse_path("~/"),
            ParsedPath(absolute=True, parts=(), trailing_slash=True),
        )

    def test_tilde_name_is_ordinary_relative_name(self):
        self.assertEqual(
            parse_path("~name"),
            ParsedPath(absolute=False, parts=("~name",), trailing_slash=False),
        )
        self.assertEqual(
            parse_path("~name/子"),
            ParsedPath(absolute=False, parts=("~name", "子"), trailing_slash=False),
        )

    def test_consecutive_slashes_collapse(self):
        parsed = parse_path("a//b//")
        self.assertEqual(parsed.parts, ("a", "b"))
        self.assertTrue(parsed.trailing_slash)
        self.assertFalse(parsed.absolute)

    def test_leading_slashes(self):
        self.assertEqual(
            parse_path("//a/b"),
            ParsedPath(absolute=True, parts=("a", "b"), trailing_slash=False),
        )

    def test_relative_and_trailing_slash(self):
        self.assertEqual(
            parse_path("目录/"),
            ParsedPath(absolute=False, parts=("目录",), trailing_slash=True),
        )

    def test_absolute_preserves_dot_and_dotdot(self):
        parsed = parse_path("/a/./b/../c")
        self.assertEqual(parsed.parts, ("a", ".", "b", "..", "c"))
        self.assertTrue(parsed.absolute)

    def test_empty_path_is_invalid(self):
        with self.assertRaises(InvalidArgument) as ctx:
            parse_path("")
        self.assertEqual(ctx.exception.code, "invalid_argument")

    def test_non_string_path_is_invalid(self):
        with self.assertRaises(InvalidArgument):
            parse_path(None)  # type: ignore[arg-type]


class TestJsonValues(unittest.TestCase):
    def test_freeze_produces_read_only_deep_structure(self):
        frozen = freeze_json({"a": [1, {"b": None}], "c": "x"})
        self.assertIsInstance(frozen, Mapping)
        self.assertIsInstance(frozen["a"], tuple)
        self.assertIsInstance(frozen["a"][1], Mapping)
        with self.assertRaises(TypeError):
            frozen["c"] = "y"  # type: ignore[index]
        with self.assertRaises(TypeError):
            frozen["a"][1]["b"] = 2  # type: ignore[index]

    def test_thaw_returns_independent_copy(self):
        frozen = freeze_json({"a": [{"b": None}]})
        first = thaw_json(frozen)
        second = thaw_json(frozen)
        self.assertIsInstance(first, dict)
        self.assertIsInstance(first["a"], list)
        self.assertIsInstance(first["a"][0], dict)
        first["a"][0]["b"] = 5
        self.assertIsNone(second["a"][0]["b"])
        self.assertIsNone(frozen["a"][0]["b"])

    def test_json_equal_boolean_is_not_number(self):
        self.assertFalse(json_equal(True, 1))
        self.assertFalse(json_equal(False, 0))
        self.assertFalse(json_equal({"a": True}, {"a": 1}))
        self.assertTrue(json_equal(True, True))

    def test_json_equal_ignores_object_key_order(self):
        self.assertTrue(json_equal({"a": 1, "b": 2}, {"b": 2, "a": 1}))
        self.assertFalse(json_equal({"a": 1}, {"a": 1, "b": 2}))

    def test_json_equal_arrays_and_numbers(self):
        self.assertTrue(json_equal([1, 2], (1, 2)))
        self.assertTrue(json_equal(1, 1.0))
        self.assertFalse(json_equal([1, 2], [2, 1]))
        self.assertFalse(json_equal(None, 0))

    def test_frozen_json_compares_equal_to_plain(self):
        self.assertTrue(json_equal(freeze_json({"a": [1, {"b": 2}]}), {"a": [1, {"b": 2}]}))


class TestMetadataValidation(unittest.TestCase):
    def test_accepts_valid_json_types(self):
        validate_metadata(
            {
                "文本": "value",
                "none": None,
                "bool": True,
                "int": 1,
                "float": 1.5,
                "list": [1, "a", None, {"nested": "x"}],
                "obj": {"键": "值"},
            }
        )

    def test_rejects_non_mapping_top_level(self):
        with self.assertRaises(InvalidArgument):
            validate_metadata(["not", "a", "mapping"])  # type: ignore[arg-type]

    def test_rejects_nested_non_string_keys(self):
        with self.assertRaises(InvalidArgument):
            validate_metadata({"ok": {1: "bad"}})
        with self.assertRaises(InvalidArgument):
            validate_metadata({"ok": [{2: "bad"}]})
        with self.assertRaises(InvalidArgument):
            validate_metadata({3: "bad"})  # type: ignore[dict-item]

    def test_rejects_nan_and_infinity(self):
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=bad):
                with self.assertRaises(InvalidArgument):
                    validate_metadata({"n": bad})
                with self.assertRaises(InvalidArgument):
                    validate_metadata({"n": [bad]})

    def test_rejects_non_json_values(self):
        for bad in ((1, 2), {1, 2}, b"bytes", object()):
            with self.subTest(value=bad):
                with self.assertRaises(InvalidArgument):
                    validate_metadata({"v": bad})

    def test_rejects_structural_fields_by_default(self):
        for key in (
            "id",
            "kind",
            "parent_id",
            "name",
            "position",
            "version",
            "revision_id",
            "created_at",
            "modified_at",
            "deleted_at",
            "workspace_id",
            "branch_id",
            "object_id",
            "current_revision_id",
        ):
            with self.subTest(key=key):
                with self.assertRaises(InvalidArgument):
                    validate_metadata({key: "x"})

    def test_reserved_keys_allowed_when_disabled(self):
        validate_metadata({"id": "x", "version": 1}, reject_reserved=False)

    def test_none_is_a_value_not_a_deletion(self):
        validate_metadata({"note": None})


class TestModels(unittest.TestCase):
    _TIMES = ("2026-09-30T00:00:00+00:00", "2026-09-30T00:00:00+00:00")

    def _node(self, **overrides):
        values = dict(
            id="n1",
            kind="folder",
            name="n1",
            parent_id=None,
            position=0,
            version=1,
            path="/n1",
            created_at=self._TIMES[0],
            modified_at=self._TIMES[1],
            deleted_at=None,
            metadata={"tags": ["a", "b"]},
        )
        values.update(overrides)
        return NodeSnapshot(**values)

    def test_content_scope_is_frozen_value(self):
        scope = ContentScope(workspace_id="w", branch_id="b", root_id="r")
        self.assertEqual(scope.workspace_id, "w")
        with self.assertRaises(dataclasses.FrozenInstanceError):
            scope.root_id = "other"  # type: ignore[misc]

    def test_node_metadata_is_deeply_frozen(self):
        node = self._node()
        self.assertIsInstance(node.metadata, Mapping)
        self.assertIsInstance(node.metadata["tags"], tuple)
        with self.assertRaises(TypeError):
            node.metadata["new"] = 1  # type: ignore[index]
        with self.assertRaises(dataclasses.FrozenInstanceError):
            node.name = "changed"  # type: ignore[misc]

    def test_node_equality_and_construction(self):
        self.assertEqual(self._node(), self._node())
        self.assertNotEqual(self._node(version=2), self._node())

    def test_frozen_snapshot_is_hashable_and_copyable(self):
        node = self._node()
        self.assertIsInstance(hash(node), int)
        self.assertEqual(copy.deepcopy(node), node)
        self.assertTrue(
            json_equal(dataclasses.asdict(node)["metadata"], {"tags": ["a", "b"]})
        )

    def test_document_snapshot_extends_node(self):
        doc = DocumentSnapshot(
            id="d1",
            kind="document",
            name="d1",
            parent_id="n1",
            position=1,
            version=3,
            path="/n1/d1",
            created_at=self._TIMES[0],
            modified_at=self._TIMES[1],
            deleted_at=None,
            metadata={},
            content="正文",
            revision_id="rev1",
        )
        self.assertIsInstance(doc, NodeSnapshot)
        self.assertEqual(doc.content, "正文")
        self.assertEqual(doc.revision_id, "rev1")

    def test_tree_item_holds_node_and_depth(self):
        item = TreeItem(node=self._node(), depth=2)
        self.assertEqual(item.depth, 2)
        self.assertIsInstance(item.node, NodeSnapshot)

    def test_delete_snapshot_collections_are_tuples(self):
        snapshot = DeleteSnapshot(
            object_id="n1",
            version=1,
            items=[TreeItem(node=self._node(), depth=0)],
            subtree_token="abc",
        )
        self.assertIsInstance(snapshot.items, tuple)
        self.assertEqual(snapshot.items[0].node.id, "n1")


class TestErrors(unittest.TestCase):
    def test_all_domain_errors_share_base_and_have_stable_codes(self):
        expected = {
            NotFound: "not_found",
            AlreadyExists: "already_exists",
            NotDirectory: "not_directory",
            NotDocument: "not_document",
            InvalidName: "invalid_name",
            InvalidArgument: "invalid_argument",
            PathOutsideRoot: "path_outside_root",
            Conflict: "conflict",
            DirectoryNotEmpty: "directory_not_empty",
            ProtectedNode: "protected_node",
            InvalidMove: "invalid_move",
            StorageBusy: "storage_busy",
            UnsupportedSchema: "unsupported_schema",
            MigrationError: "migration_error",
        }
        for cls, code in expected.items():
            with self.subTest(cls=cls.__name__):
                error = cls("message")
                self.assertIsInstance(error, ContentError)
                self.assertEqual(error.code, code)
                self.assertEqual(error.details, {})

    def test_details_are_structured_and_detached(self):
        source = {"object_id": "x"}
        error = NotFound("missing", details=source)
        self.assertEqual(error.details, {"object_id": "x"})
        source["object_id"] = "y"
        self.assertEqual(error.details["object_id"], "x")

    def test_error_is_an_exception_with_message(self):
        error = Conflict("version mismatch", details={"expected": 1, "actual": 2})
        self.assertIsInstance(error, Exception)
        self.assertEqual(str(error), "version mismatch")
        self.assertEqual(error.details["actual"], 2)


if __name__ == "__main__":
    unittest.main()


class TestCyclicMetadata(unittest.TestCase):
    def test_cyclic_metadata_is_invalid_argument(self):
        value = {}
        value["self"] = value
        with self.assertRaises(InvalidArgument):
            validate_metadata(value)

    def test_shared_acyclic_containers_are_valid(self):
        shared = {"items": [1, None]}
        validate_metadata({"left": shared, "right": shared})
