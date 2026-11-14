import { PLAY_SPEEDS, useSelection } from '../store/useSelection';

/** Cycles the playback speed: ½× → 1× → 2× → 4× → 8× (continuous: days blend into each other). */
export default function SpeedButton({ className = '' }: { className?: string }) {
  const { speed, setSpeed } = useSelection();
  const next = PLAY_SPEEDS[(PLAY_SPEEDS.indexOf(speed) + 1) % PLAY_SPEEDS.length];
  return (
    <button
      onClick={() => setSpeed(next)}
      className={`tabular rounded-full text-cap font-medium text-ink-2 transition-colors hover:bg-raised hover:text-ink ${className}`}
      aria-label={`Playback speed ${speed}×, change to ${next}×`}
      title="Playback speed"
    >
      {speed === 0.5 ? '½' : speed}×
    </button>
  );
}
