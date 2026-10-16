import { Prose } from "./PublicLayout";

export function AboutPage() {
  return (
    <Prose>
      <h1>About EarthScape Climate Agency</h1>
      <p>
        EarthScape Climate Agency is an enterprise big-data climate intelligence platform built for
        a university eProject. It demonstrates the full lifecycle of operational climate data:
        ingesting live observations from public provider APIs, validating and storing them on a
        distributed data lake, running batch analytics, detecting anomalies, training forecasting
        models, and presenting the results through governed dashboards, maps and reports.
      </p>
      <h2>The real architecture</h2>
      <ul>
        <li>
          <strong>System of record:</strong> MongoDB stores users, datasets, raw and validated
          observations, ingestion jobs, alert rules and events, analytics summaries, predictions and
          reports.
        </li>
        <li>
          <strong>Data lake:</strong> an HDFS-style lake (WebHDFS when Hadoop is configured, a local
          filesystem otherwise) holds raw, validated, processed, analytics and ML-feature layers.
        </li>
        <li>
          <strong>Processing:</strong> Spark and Hadoop/MapReduce batch engines compute trends,
          correlations, quality and feature summaries from stored observations.
        </li>
        <li>
          <strong>Machine learning:</strong> a model registry and prediction jobs generate forecasts
          that are stored alongside their source data.
        </li>
        <li>
          <strong>Sources:</strong> Open-Meteo (archive + forecast) and NASA POWER are live and
          keyless; NOAA Climate Data Online is wired but requires an API token on this deployment.
        </li>
      </ul>
      <h2>Our data principle</h2>
      <p>
        Every figure in the product comes from the running API. When a collection is empty the
        interface says so and gives the reason the API returned — it never renders placeholder or
        demo values. Correlation output always carries the caveat that association is not causation.
      </p>
    </Prose>
  );
}
