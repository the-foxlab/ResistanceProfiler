export function DatabaseSelectorBar({
  databases,
  selectedDatabase,
  selectedDatabaseId,
  onDatabaseChange,
  selectId,
  className,
}) {
  return (
    <div className={`header-db-bar ${className || ''}`.trim()}>
      {databases.length > 0 ? (
        <>
          {/* One shared selector component keeps database switching identical across tabs. */}
          <label className="header-db-label" htmlFor={selectId}>Database</label>
          <select
            id={selectId}
            className="header-db-select"
            value={selectedDatabaseId}
            onChange={(event) => onDatabaseChange(event.target.value)}
          >
            {databases.map((database) => (
              <option key={database.id} value={database.id}>{database.display_name}</option>
            ))}
          </select>
          {selectedDatabase ? (
            // Quick context badge for currently loaded mutation entry count.
            <span className="header-db-badge">{selectedDatabase.mutation_count} entries</span>
          ) : null}
        </>
      ) : (
        <span className="status">No databases available</span>
      )}
    </div>
  );
}
