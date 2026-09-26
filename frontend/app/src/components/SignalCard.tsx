import type { SignalCard as Card } from "../lib/contracts";
import { cardModel, CONF_TEXT } from "../lib/cardModel.js";

// Clinical Signal Card v0 in the app (justin.md R3, "What my doctor sees").
// Draws the SAME model (lib/cardModel.js = clinician/card-model.js) with the
// SAME sc-* classes (index.css = clinician/card.css) as the clinician inbox,
// so a card looks identical in both. The patient's view shows no actions.
export default function SignalCard({ card, sample = false }: { card: Card; sample?: boolean }) {
  const m = cardModel(card, { sample });
  return (
    <article className={`sc-card ${m.statusCls}`}>
      <div className="sc-badges">
        {m.badges.map((b, i) => (
          <span key={i} className={`sc-badge ${b.cls}`.trim()}>
            {b.text}
          </span>
        ))}
      </div>
      <div className="sc-meta">{m.meta}</div>
      <p className="sc-headline">{m.headline}</p>

      {m.flags.length > 0 && (
        <div className="sc-flags">
          {m.flags.map((f) => (
            <span key={f} className="sc-flag">
              {f}
            </span>
          ))}
        </div>
      )}
      {m.banner && <div className="sc-banner">{m.banner}</div>}

      {m.rows.length > 0 && (
        <section className="sc-section">
          <h4 className="sc-h">Numbers</h4>
          <table className="sc-metrics">
            <tbody>
              {m.rows.map((r) => (
                <tr key={r.key}>
                  <td>{r.label}</td>
                  {r.value === null ? <td className="sc-v sc-nodata">no data</td> : <td className="sc-v">{r.value}</td>}
                  <td className="sc-c">
                    {r.conf ? (
                      <span className={`sc-conf sc-${r.conf}`}>{CONF_TEXT[r.conf] ?? r.conf}</span>
                    ) : (
                      <span className="sc-conf sc-nolabel">no label</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      {m.nights.length > 0 && (
        <section className="sc-section">
          <h4 className="sc-h">Nights</h4>
          <div className="sc-strip">
            {m.nights.map((nt, i) => (
              <div
                key={i}
                title={nt.title}
                className={`sc-night ${nt.cls} ${nt.source === "logged" ? "sc-logged" : "sc-inferred-code"}`}
              >
                <b>{nt.date}</b>
                {nt.text}
              </div>
            ))}
          </div>
          <div className="sc-legend">Solid border: reason logged. Dashed: inferred from the trace.</div>
        </section>
      )}

      {(m.excluded.length > 0 || m.excludedCounts) && (
        <section className="sc-section">
          <h4 className="sc-h">Left out of the comparison</h4>
          {m.excludedCounts && <div className="sc-meta">{m.excludedCounts}</div>}
          {m.excluded.length > 0 && (
            <ul className="sc-list">
              {m.excluded.map((x, i) => (
                <li key={i}>{`${x.date}: ${x.reasons}`}</li>
              ))}
            </ul>
          )}
        </section>
      )}

      {m.tolerance.length > 0 && (
        <section className="sc-section">
          <h4 className="sc-h">Stomach, day by day</h4>
          <div className="sc-gi">
            {m.tolerance.map((d, i) => (
              <span key={i} className={d.cls}>{`${d.date} ${d.text}`}</span>
            ))}
          </div>
        </section>
      )}

      {m.resources.length > 0 && (
        <section className="sc-section">
          <h4 className="sc-h">Resources</h4>
          <div>{m.resources.join(" · ")}</div>
        </section>
      )}

      <section className="sc-section">
        <h4 className="sc-h">Summary</h4>
        <p className="sc-narrative">{m.narrative}</p>
      </section>

      <div className="sc-foot">{m.foot}</div>
    </article>
  );
}
