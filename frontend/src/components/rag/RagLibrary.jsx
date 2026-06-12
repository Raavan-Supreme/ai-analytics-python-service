import { useState } from "react";
import toast from "react-hot-toast";
import { parseApiError, ragApi } from "../../services/api";

export default function RagLibrary() {
  const [source, setSource] = useState("");
  const [question, setQuestion] = useState("");
  const [answer, setAnswer] = useState("");

  const indexDocs = async () => {
    try {
      await ragApi.index({ source });
      toast.success("Document indexed");
      setSource("");
    } catch (error) {
      toast.error(parseApiError(error, "Indexing failed"));
    }
  };

  const ask = async () => {
    try {
      const { data } = await ragApi.answer({ question });
      setAnswer(data.answer || "No answer returned");
    } catch (error) {
      toast.error(parseApiError(error, "Could not retrieve grounded answer"));
    }
  };

  return (
    <section className="stack">
      <div className="panel">
        <h2>RAG Library</h2>
        <p>Index docs and query with grounded retrieval.</p>
        <div className="inline-form">
          <input value={source} onChange={(e) => setSource(e.target.value)} placeholder="Path or URL" />
          <button className="btn-primary" onClick={indexDocs}>Index</button>
        </div>
      </div>
      <div className="panel">
        <h3>Ask grounded question</h3>
        <div className="inline-form">
          <input value={question} onChange={(e) => setQuestion(e.target.value)} placeholder="What changed in quarter 2?" />
          <button className="btn-primary" onClick={ask}>Ask</button>
        </div>
        {answer && <p className="answer">{answer}</p>}
      </div>
    </section>
  );
}
