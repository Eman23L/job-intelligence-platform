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

  return (
    <section className="filters-bar">
      <label>
        Role family
        <input value={filters.role_family} onChange={(event) => update("role_family", event.target.value)} />
      </label>
      <label>
        Tier
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
        Remote type
        <select value={filters.remote_type} onChange={(event) => update("remote_type", event.target.value)}>
          <option value="">Any</option>
          <option value="remote">Remote</option>
          <option value="hybrid">Hybrid</option>
          <option value="onsite">Onsite</option>
        </select>
      </label>
      <label>
        Location
        <input value={filters.location} onChange={(event) => update("location", event.target.value)} />
      </label>
      <label>
        Company
        <input value={filters.company_name} onChange={(event) => update("company_name", event.target.value)} />
      </label>
      <label>
        Min score
        <input type="number" value={filters.min_score} onChange={(event) => update("min_score", event.target.value)} />
      </label>
      <label>
        Max score
        <input type="number" value={filters.max_score} onChange={(event) => update("max_score", event.target.value)} />
      </label>
      <label>
        Availability
        <select value={filters.availability_status} onChange={(event) => update("availability_status", event.target.value)}>
          <option value="">Any</option>
          <option value="active">Active</option>
          <option value="unknown">Unknown</option>
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
      <label>
        Sort
        <select value={filters.sort} onChange={(event) => update("sort", event.target.value)}>
          <option value="total_score_desc">Score</option>
          <option value="posted_at_desc">Posted date</option>
          <option value="salary_max_desc">Salary max</option>
          <option value="salary_min_desc">Salary min</option>
          <option value="company_name_asc">Company</option>
          <option value="title_asc">Title</option>
        </select>
      </label>
      <label className="checkbox-label">
        <input
          type="checkbox"
          checked={filters.exclude_excluded}
          onChange={(event) => update("exclude_excluded", event.target.checked)}
        />
        Hide excluded jobs
      </label>
      <button type="button" className="secondary-button" onClick={onReset}>
        Reset
      </button>
    </section>
  );
}
