export function Spinner() {
  return (
    <svg className="spinner" viewBox="0 0 50 50" xmlns="http://www.w3.org/2000/svg">
      <circle cx="25" cy="25" r="20" fill="none" stroke="currentColor" strokeWidth="4" opacity="0.3" />
      <circle cx="25" cy="25" r="20" fill="none" stroke="currentColor" strokeWidth="4" strokeDasharray="30" />
    </svg>
  );
}
