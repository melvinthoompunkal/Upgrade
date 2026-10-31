import type { Catalog, CatalogFeature } from '../../api/client'

export function FeatureDownload({
  catalog,
  feature,
}: {
  catalog: Catalog
  feature: CatalogFeature
}) {
  function download() {
    const data = {
      schema_version:
        feature.schema_version === 'catalog-feature-v2'
          ? 'source-feature-export-v2'
          : 'source-feature-export-v1',
      analysis_identity: catalog.identity,
      coverage: catalog.coverage_v2,
      catalog_id: catalog.id,
      catalog_revision: catalog.revision,
      source_project: catalog.source_project,
      source_sha: catalog.source_sha,
      project_root: catalog.project_root,
      scope: catalog.scope,
      qualification: catalog.qualification,
      feature,
    }
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }),
    )
    const link = document.createElement('a')
    link.href = url
    link.download = `feature-${feature.feature_id}.json`
    link.click()
    setTimeout(() => URL.revokeObjectURL(url), 0)
  }
  return (
    <button type="button" className="button-secondary" onClick={download}>
      Download feature JSON
    </button>
  )
}
