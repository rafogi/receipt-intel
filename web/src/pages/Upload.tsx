import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useApi } from "../App";
import { type PreparedPhoto, preparePhoto } from "../imageCheck";
import { PROBLEM_LABELS } from "../labels";

type Stage = "pick" | "checking" | "review" | "uploading";

export function Upload() {
  const api = useApi();
  const navigate = useNavigate();
  const [stage, setStage] = useState<Stage>("pick");
  const [photo, setPhoto] = useState<PreparedPhoto | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Free the preview image when it's replaced or the page closes.
  useEffect(() => {
    return () => {
      if (photo) URL.revokeObjectURL(photo.previewUrl);
    };
  }, [photo]);

  async function onFile(file: File | undefined) {
    if (!file) return;
    setError(null);
    setStage("checking");
    try {
      setPhoto(await preparePhoto(file));
      setStage("review");
    } catch (err) {
      setError(`Couldn't open that photo: ${(err as Error).message}`);
      setStage("pick");
    }
  }

  async function upload() {
    if (!photo) return;
    setStage("uploading");
    setError(null);
    try {
      const id = await api.uploadPhoto(photo.blob);
      navigate(`/receipts/${id}`, { state: { justUploaded: true } });
    } catch (err) {
      setError((err as Error).message);
      setStage("review");
    }
  }

  function retake() {
    setPhoto(null);
    setStage("pick");
  }

  return (
    <main className="page">
      <h1>Add a receipt</h1>
      {error && <p className="error">{error}</p>}

      {stage === "pick" && (
        <div className="stack">
          <p className="muted">Lay the receipt flat in good light and fill the frame.</p>
          <label className="button primary">
            Take photo
            <input
              type="file"
              accept="image/*"
              capture="environment"
              hidden
              onChange={(e) => onFile(e.target.files?.[0])}
            />
          </label>
          <label className="button secondary">
            Choose from library
            <input type="file" accept="image/*" hidden onChange={(e) => onFile(e.target.files?.[0])} />
          </label>
        </div>
      )}

      {stage === "checking" && <p className="muted">Checking the photo…</p>}

      {photo && (stage === "review" || stage === "uploading") && (
        <div className="stack">
          <img src={photo.previewUrl} alt="Receipt preview" className="preview" />
          {photo.problems.length > 0 ? (
            <div className="warning">
              <ul>
                {photo.problems.map((p) => (
                  <li key={p}>{PROBLEM_LABELS[p]}</li>
                ))}
              </ul>
              <p>A retake usually reads better. You can still upload it.</p>
            </div>
          ) : (
            <p className="ok">Looks sharp and bright enough.</p>
          )}
          <button className="primary" disabled={stage === "uploading"} onClick={upload}>
            {stage === "uploading" ? "Uploading…" : photo.problems.length ? "Upload anyway" : "Upload"}
          </button>
          <button className="secondary" disabled={stage === "uploading"} onClick={retake}>
            Retake
          </button>
        </div>
      )}
    </main>
  );
}
