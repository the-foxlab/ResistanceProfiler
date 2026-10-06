import { AboutTab } from '../tabs/AboutTab';

export function AboutPage({ logic }) {
  return (
    <div className="page-about">
      <AboutTab contactEmail={logic.contactEmail} />
    </div>
  );
}
