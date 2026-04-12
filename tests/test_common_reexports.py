# packages/rots/tests/test_common_reexports.py

"""Tests for rots.commands.common — re-exports from ots_shared."""

import ots_shared.cli
import ots_shared.exit_codes

from rots.commands.common import (
    EXIT_FAILURE,
    EXIT_PARTIAL,
    EXIT_PRECOND,
    EXIT_SUCCESS,
    DryRun,
    Follow,
    ImageRef,
    JsonOutput,
    Lines,
    Quiet,
    TagFlag,
    Yes,
)


class TestCliAliasReexports:
    """Re-exported CLI aliases must be the same objects as ots_shared.cli."""

    def test_dry_run_is_same_object(self):
        assert DryRun is ots_shared.cli.DryRun

    def test_quiet_is_same_object(self):
        assert Quiet is ots_shared.cli.Quiet

    def test_yes_is_same_object(self):
        assert Yes is ots_shared.cli.Yes

    def test_follow_is_same_object(self):
        assert Follow is ots_shared.cli.Follow

    def test_lines_is_same_object(self):
        assert Lines is ots_shared.cli.Lines

    def test_json_output_is_same_object(self):
        assert JsonOutput is ots_shared.cli.JsonOutput


class TestExitCodeReexports:
    """Re-exported exit codes must be the ots_shared.exit_codes attributes."""

    def test_exit_success(self):
        from rots.commands import common

        assert common.EXIT_SUCCESS is ots_shared.exit_codes.EXIT_SUCCESS
        assert EXIT_SUCCESS == ots_shared.exit_codes.EXIT_SUCCESS

    def test_exit_failure(self):
        from rots.commands import common

        assert common.EXIT_FAILURE is ots_shared.exit_codes.EXIT_FAILURE
        assert EXIT_FAILURE == ots_shared.exit_codes.EXIT_FAILURE

    def test_exit_partial(self):
        from rots.commands import common

        assert common.EXIT_PARTIAL is ots_shared.exit_codes.EXIT_PARTIAL
        assert EXIT_PARTIAL == ots_shared.exit_codes.EXIT_PARTIAL

    def test_exit_precond(self):
        from rots.commands import common

        assert common.EXIT_PRECOND is ots_shared.exit_codes.EXIT_PRECOND
        assert EXIT_PRECOND == ots_shared.exit_codes.EXIT_PRECOND


class TestRotsSpecificAliases:
    """ImageRef and TagFlag are rots-specific and should not come from ots_shared."""

    def test_image_ref_is_annotated(self):
        import typing

        assert typing.get_origin(ImageRef) is typing.Annotated

    def test_tag_flag_is_annotated(self):
        import typing

        assert typing.get_origin(TagFlag) is typing.Annotated

    def test_image_ref_not_in_ots_shared_cli(self):
        assert not hasattr(ots_shared.cli, "ImageRef")

    def test_tag_flag_not_in_ots_shared_cli(self):
        assert not hasattr(ots_shared.cli, "TagFlag")


class TestAllExports:
    """__all__ should list every public name."""

    def test_all_contains_cli_aliases(self):
        from rots.commands import common

        for name in ("DryRun", "Quiet", "Yes", "Follow", "Lines", "JsonOutput"):
            assert name in common.__all__

    def test_all_contains_exit_codes(self):
        from rots.commands import common

        for name in ("EXIT_SUCCESS", "EXIT_FAILURE", "EXIT_PARTIAL", "EXIT_PRECOND"):
            assert name in common.__all__

    def test_all_contains_rots_specific(self):
        from rots.commands import common

        for name in ("ImageRef", "TagFlag"):
            assert name in common.__all__
