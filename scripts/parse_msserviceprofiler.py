#!/usr/bin/env python3
"""Run msServiceProfiler with the Pandas trace-export compatibility fix.

Some container combinations expose the ``rid`` column as Pandas StringDtype.
The installed exporter assigns ``str.split(',')`` lists back to that column,
which newer Pandas versions reject.  Convert only that working column to
object dtype before delegating to the vendor exporter.
"""

from ms_service_profiler.exporters import exporter_trace


_original_add_flow_event = exporter_trace.add_flow_event


def _add_flow_event_compat(flow_event_df):
    if "rid" in flow_event_df.columns:
        flow_event_df = flow_event_df.copy()
        # Pandas may report either ``string`` or ``string[python]`` here.
        # Converting unconditionally is safe and keeps the patch compatible
        # with both older and newer Pandas releases.
        flow_event_df["rid"] = flow_event_df["rid"].astype(object)
    return _original_add_flow_event(flow_event_df)


exporter_trace.add_flow_event = _add_flow_event_compat

from ms_service_profiler.__main__ import main  # noqa: E402


if __name__ == "__main__":
    main()
