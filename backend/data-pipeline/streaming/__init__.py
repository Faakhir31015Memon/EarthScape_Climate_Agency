"""Streaming ingestion over the data lake (batched micro-streams).

Both paths emit canonical observations validated by ``validation`` and checked
for schema equality by ``streaming.schema_compat``.
"""

from streaming.schema_compat import (assert_batch_stream_compatible,  # noqa: F401
                                     assert_canonical, assert_same_field_set)
