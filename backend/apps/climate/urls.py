from django.urls import path

from apps.climate import views

urlpatterns = [
    path("observations", views.ObservationListView.as_view(), name="climate-observations"),
    path("trends", views.TrendView.as_view(), name="climate-trends"),
    path("regions", views.RegionListView.as_view(), name="climate-regions"),
    path("regions/<str:code>", views.RegionDetailView.as_view(), name="climate-region-detail"),
    path("anomalies", views.AnomalyListView.as_view(), name="climate-anomalies"),
    path("anomalies/scan", views.AnomalyScanView.as_view(), name="climate-anomaly-scan"),
    path("map-data", views.MapDataView.as_view(), name="climate-map-data"),
    path("correlations", views.CorrelationView.as_view(), name="climate-correlations"),
    path("comparison", views.ComparisonView.as_view(), name="climate-comparison"),
    path("history", views.HistoryView.as_view(), name="climate-history"),
    path("quality", views.QualityView.as_view(), name="climate-quality"),
    path("stations", views.StationListView.as_view(), name="climate-stations"),
    path("metrics", views.MetricCatalogueView.as_view(), name="climate-metrics"),
    path("filter-options", views.FilterOptionsView.as_view(), name="climate-filter-options"),
    path("export", views.ObservationExportView.as_view(), name="climate-export"),
]
