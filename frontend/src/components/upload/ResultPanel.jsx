const normalizeTable = (result) => {
  if (Array.isArray(result?.table)) return result.table;
  if (Array.isArray(result?.rows)) return result.rows;
  return [];
};

const resolveSummary = (result) => (
  result?.summary
  || result?.answer
  || result?.message
  || result?.result
  || "Query completed."
);

export default function ResultPanel({ result, history = [] }) {
  const table = normalizeTable(result);

  return (
    <div className="panel">
      <h3>Result</h3>
      {!result ? <p>No result yet.</p> : (
        <>
          <p>{resolveSummary(result)}</p>
          {result.source && (
            <p>
              Source: {result.source.type || "all"}
              {result.source.id ? ` #${result.source.id}` : ""}
            </p>
          )}
          {table.length > 0 && (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>{Object.keys(table[0] || {}).map((col) => <th key={col}>{col}</th>)}</tr>
                </thead>
                <tbody>
                  {table.map((row, idx) => (
                    <tr key={idx}>
                      {Object.values(row).map((val, innerIdx) => <td key={innerIdx}>{String(val)}</td>)}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {result.chartUrl && <img src={result.chartUrl} alt="Generated chart" className="chart" />}
        </>
      )}

      <h3>Recent Questions & Answers</h3>
      {history.length === 0 ? (
        <p>No questions asked yet.</p>
      ) : (
        <ul className="list-clean">
          {history.map((item) => (
            <li key={item.id}>
              <strong>Q:</strong> {item.question}
              <br />
              <strong>A:</strong> {resolveSummary(item.answer)}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
