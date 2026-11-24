import infoIconSrc from '../assets/info.svg';

export function InfoButton({ label, title }) {
  return (
    <button type="button" className="input-info-btn" aria-label={label} title={title}>
      <img className="input-info-icon" src={infoIconSrc} alt="" aria-hidden="true" />
    </button>
  );
}
