import { InfoButton } from '../../InfoButton';

export function VcfCutoffsFields({
  minAf,
  minDepth,
  disabled,
  onChangeMinAf,
  onChangeMinDepth,
  detailsClassName = 'profile-advanced-options',
  labelClassName,
  freqTourTarget,
  covTourTarget,
  freqHelpLabel,
  freqHelpTitle,
  covHelpLabel,
  covHelpTitle,
}) {
  return (
    <details className={detailsClassName}>
      <summary>Advanced options</summary>
      <div className="profile-advanced-options-fields">
        <label className={labelClassName} data-tour-target={freqTourTarget}>
          <span className="label-text input-label-row">
            Frequency cutoff <InfoButton label={freqHelpLabel} title={freqHelpTitle} />
          </span>
          <input
            type="number"
            min="0"
            max="1"
            step="0.001"
            value={minAf}
            disabled={disabled}
            onChange={(event) => {
              const value = Number(event.target.value);
              if (Number.isFinite(value)) {
                onChangeMinAf(value);
              }
            }}
          />
        </label>
        <label className={labelClassName} data-tour-target={covTourTarget}>
          <span className="label-text input-label-row">
            Coverage cutoff <InfoButton label={covHelpLabel} title={covHelpTitle} />
          </span>
          <input
            type="number"
            min="0"
            step="1"
            value={minDepth}
            disabled={disabled}
            onChange={(event) => {
              const value = Number(event.target.value);
              if (Number.isFinite(value)) {
                onChangeMinDepth(Math.trunc(value));
              }
            }}
          />
        </label>
      </div>
    </details>
  );
}
