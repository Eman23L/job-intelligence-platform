export interface JobFiltersState {
  role_family: string;
  recommendation_tier: string;
  remote_type: string;
  location: string;
  company_name: string;
  min_score: string;
  max_score: string;
  exclude_excluded: boolean;
  availability_status: string;
  apply_difficulty: string;
  source_id: string;
  sort: string;
}

export function FiltersBar({
  filters,
  onChange,
  onReset
}: {
  filters: JobFiltersState;
  onChange: (next: JobFiltersState) => void;
  onReset: () => void;
}) {
  const update = (key: keyof JobFiltersState, value: string | boolean) => {
    onChange({ ...filters, [key]: value });
  };

  const advancedCount = (
    ["role_family", "company_name", "min_score", "max_score", "availability_status", "apply_difficulty"] as const
  ).filter((key) => filters[key].trim()).length;

  return (
    <section className="filters-bar" aria-label="Filter jobs">
      <label>
        Location
        <input placeholder="e.g. London" value={filters.location} onChange={(event) => update("location", event.target.value)} />
      </label>
      <label>
        Work style
        <select value={filters.remote_type} onChange={(event) => update("remote_type", event.target.value)}>
          <option value="">Any</option>
          <option value="remote">Remote</option>
          <option value="hybrid">Hybrid</option>
          <option value="onsite">On-site</option>
        </select>
      </label>
      <label>
        Match
        <select value={filters.recommendation_tier} onChange={(event) => update("recommendation_tier", event.target.value)}>
          <option value="">Any</option>
          <option value="Excellent match">Excellent match</option>
          <option value="Strong match">Strong match</option>
          <option value="Possible match">Possible match</option>
          <option value="Stretch role">Stretch role</option>
          <option value="Poor fit">Poor fit</option>
          <option value="excluded">Excluded</option>
        </select>
      </label>
      <label>
        Sort by
        <select value={filters.sort} onChange={(event) => update("sort", event.target.value)}>
          <option value="total_score_desc">Best match</option>
          <option value="posted_at_desc">Newest</option>
          <option value="salary_max_desc">Highest salary</option>
          <option value="company_name_asc">Company A–Z</option>
          <option value="title_asc">Title A–Z</option>
        </select>
      </label>
      <details className="filters-more" open={advancedCount > 0 ? true : undefined}>
        <summary>{advancedCount > 0 ? `More filters (${advancedCount})` : "More filters"}</summary>
        <div className="filters-more-grid">
          <label>
            Role family
            <input placeholder="e.g. Data Engineer" value={filters.role_family} onChange={(event) => update("role_family", event.target.value)} />
          </label>
          <label>
            Company
            <input value={filters.company_name} onChange={(event) => update("company_name", event.target.value)} />
          </label>
          <label>
            Min score
            <input type="number" min={0} max={100} value={filters.min_score} onChange={(event) => update("min_score", event.target.value)} />
          </label>
          <label>
            Max score
            <input type="number" min={0} max={100} value={filters.max_score} onChange={(event) => update("max_score", event.target.value)} />
          </label>
          <label>
            Availability
            <select value={filters.availability_status} onChange={(event) => update("availability_status", event.target.value)}>
              <option value="">Any</option>
              <option value="active">Open</option>
              <option value="unknown">Not checked</option>
              <option value="replaced">Replaced</option>
              <option value="expired">Expired</option>
              <option value="unavailable">Unavailable</option>
              <option value="redirected">Redirected</option>
            </select>
          </label>
          <label>
            Apply difficulty
            <select value={filters.apply_difficulty} onChange={(event) => update("apply_difficulty", event.target.value)}>
              <option value="">Any</option>
              <option value="easy">Easy</option>
              <option value="medium">Medium</option>
              <option value="hard">Hard</option>
              <option value="blocked">Blocked</option>
              <option value="unknown">Unknown</option>
            </select>
          </label>
        </div>
      </details>
      <div className="filters-footer">
        <label className="checkbox-label">
          <input
            type="checkbox"
            checked={filters.exclude_excluded}
            onChange={(event) => update("exclude_excluded", event.target.checked)}
          />
          Hide excluded jobs
        </label>
        <button type="button" className="ghost-button compact-button" onClick={onReset}>
          Clear filters
        </button>
      </div>
    </section>
  );
}
