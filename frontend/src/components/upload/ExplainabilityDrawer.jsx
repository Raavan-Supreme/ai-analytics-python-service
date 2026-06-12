export default function ExplainabilityDrawer({ result }) {
  return (
    <div className="panel">
      <h3>Explainability</h3>
      {!result?.explainability ? (
        <p>Explainability details will appear after a query.</p>
      ) : (
        <ul className="list-clean">
          <li>Engine: {result.explainability.engine || "n/a"}</li>
          <li>Confidence: {result.explainability.confidence || "n/a"}</li>
          <li>Safety checks: {result.explainability.safetyChecks || "n/a"}</li>
        </ul>
      )}
    </div>
  );
}
