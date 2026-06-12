import { useState } from "react";
import { useDropzone } from "react-dropzone";
import toast from "react-hot-toast";
import { fileApi, parseApiError } from "../../services/api";

export default function UploadScreen({ tenant, onSelectView }) {
  const [files, setFiles] = useState([]);
  const [uploading, setUploading] = useState(false);

  const onDrop = (accepted) => {
    console.log("[AISX][UploadScreen] onDrop", {
      tenantId: tenant?.id,
      acceptedCount: accepted.length,
      acceptedNames: accepted.map((item) => item.name)
    });
    setFiles((prev) => [...prev, ...accepted]);
  };
  const { getRootProps, getInputProps, isDragActive } = useDropzone({ onDrop });

  const uploadAll = async () => {
    console.log("[AISX][UploadScreen] uploadAll:start", {
      tenantId: tenant?.id,
      queuedFiles: files.map((item) => item.name)
    });
    if (files.length === 0) return;
    if (!tenant?.id) {
      toast.error("Select a workspace before uploading files");
      console.warn("[AISX][UploadScreen] uploadAll:blocked - missing tenant");
      return;
    }

    setUploading(true);
    try {
      const form = new FormData();
      files.forEach((file) => form.append("files", file));
      form.append("tenantId", String(tenant.id));
      const response = await fileApi.uploadMany(form);
      console.log("[AISX][UploadScreen] uploadAll:success", {
        tenantId: tenant.id,
        response: response?.data
      });
      toast.success("Files uploaded");
      setFiles([]);
      console.log("[AISX][UploadScreen] dispatch sources-updated", { tenantId: tenant.id });
      window.dispatchEvent(new CustomEvent("sources-updated", { detail: { tenantId: tenant.id } }));
      onSelectView?.("workspace");
    } catch (error) {
      console.error("[AISX][UploadScreen] uploadAll:error", {
        tenantId: tenant?.id,
        error,
        response: error?.response?.data
      });
      toast.error(parseApiError(error, "Upload failed"));
    } finally {
      console.log("[AISX][UploadScreen] uploadAll:done", { tenantId: tenant?.id });
      setUploading(false);
    }
  };

  return (
    <section className="stack">
      <div {...getRootProps()} className={`dropzone ${isDragActive ? "active" : ""}`}>
        <input {...getInputProps()} />
        <h3>Drop datasets here</h3>
        <p>CSV, Excel, JSON, Parquet supported</p>
      </div>

      <div className="panel">
        <h3>Queued files</h3>
        <ul className="list-clean">
          {files.length === 0 ? <li>No files selected.</li> : files.map((file) => <li key={`${file.name}-${file.size}`}>{file.name}</li>)}
        </ul>
        <button className="btn-primary" onClick={uploadAll} disabled={uploading || files.length === 0}>
          {uploading ? "Uploading..." : "Upload all"}
        </button>
      </div>

      <div className="panel">
        <h3>Relationship Mapper</h3>
        <p>Map join keys across uploaded sources in backend relationship APIs.</p>
        <p>
          After uploading, open the <strong>Workspace</strong> tab and use <strong>Ask Your Data</strong>
          to ask questions like: "show top 10 products by revenue".
        </p>
      </div>
    </section>
  );
}
