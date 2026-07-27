"""The reserved reporting-table suffix is rejected at the API boundary (CASE-710).

Per-version reporting tables are named ``<base>__v<N>``; a template value
or cosmetic reporting.table_name ending in that pattern would collide with
another entity's version table (``sample__v3`` vs ``sample`` v3). The
request models reject it — the naming scheme's only collision class.
"""

import pytest
from pydantic import ValidationError

from template_store.models.api_models import (
    CreateTemplateRequest,
    UpdateTemplateRequest,
)


def _create_kwargs(**over):
    kw = {
        "value": "sample",
        "label": "Sample",
        "namespace": "testns",
        "fields": [],
    }
    kw.update(over)
    return kw


class TestValueSuffixGuard:
    def test_plain_value_accepted(self):
        req = CreateTemplateRequest(**_create_kwargs())
        assert req.value == "sample"

    @pytest.mark.parametrize("bad", ["sample__v3", "x__v0", "a__v12345"])
    def test_reserved_suffix_rejected(self, bad):
        with pytest.raises(ValidationError, match="reserved reporting suffix"):
            CreateTemplateRequest(**_create_kwargs(value=bad))

    @pytest.mark.parametrize("ok", ["sample__version", "v3", "sample_v3", "x__vNext"])
    def test_lookalikes_accepted(self, ok):
        req = CreateTemplateRequest(**_create_kwargs(value=ok))
        assert req.value == ok


class TestTableNameSuffixGuard:
    def test_create_rejects_reserved_table_name(self):
        with pytest.raises(ValidationError, match="reserved reporting suffix"):
            CreateTemplateRequest(**_create_kwargs(
                reporting={"sync_enabled": True, "table_name": "people__v2"},
            ))

    def test_create_accepts_plain_table_name(self):
        req = CreateTemplateRequest(**_create_kwargs(
            reporting={"sync_enabled": True, "table_name": "people"},
        ))
        assert req.reporting.table_name == "people"

    def test_update_rejects_reserved_table_name(self):
        with pytest.raises(ValidationError, match="reserved reporting suffix"):
            UpdateTemplateRequest(
                reporting={"sync_enabled": True, "table_name": "people__v2"},
            )

    def test_update_accepts_plain_reporting(self):
        req = UpdateTemplateRequest(reporting={"sync_enabled": True})
        assert req.reporting is not None
