import { useState } from "react";
import { PageHeader } from "../../../components/ui/PageHeader";
import { ClimateMap } from "../../../components/maps/ClimateMap";
import { useApi } from "../../../lib/api/query";
import type { MapDataResult, MapMode } from "../../../lib/api/types";

export function MapPage() {
  const [mode, setMode] = useState<MapMode>("markers");
  const { data, loading, error, refetch } = useApi<MapDataResult>(
    "/climate/map-data",
    [mode],
    { params: { mode } },
  );

  return (
    <div className="space-y-6">
      <PageHeader
        title="Climate map"
        eyebrow="Overview"
        description="Geographic summaries rendered on OpenStreetMap tiles from the map-data endpoint."
      />
      {error ? (
        <button
          type="button"
          onClick={refetch}
          className="text-sm text-primary hover:underline"
        >
          Failed to load map data: {error.message}. Retry.
        </button>
      ) : (
        <ClimateMap
          points={data?.points ?? []}
          mode={data?.mode ?? mode}
          onModeChange={setMode}
          loading={loading}
          emptyReason={data && !data.available ? data.reason ?? undefined : undefined}
        />
      )}
    </div>
  );
}
