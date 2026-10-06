import { Link } from 'react-router';

export function NotFoundPage() {
  return (
    <div className="page-not-found">
      <h1>Page not found</h1>
      <p>The page you requested does not exist.</p>
      <Link to="/">Back to home</Link>
    </div>
  );
}
