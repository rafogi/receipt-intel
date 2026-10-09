/** Typed client for the receipts HTTP API (services/pipeline/src/api.py). */

export type Status = "processing" | "needs_review" | "needs_manual_entry" | "processed";

export interface Receipt {
  receiptId: string;
  status: Status;
  reasons?: string[];
  store?: string;
  date?: string;
  subtotal?: number;
  gst?: number;
  pst?: number;
  total?: number;
  category?: string;
  source?: "pipeline" | "manual";
  uploadedAt?: string;
  processedAt?: string;
  editedAt?: string;
  photoUrl?: string;
}

/**
 * PATCH body. Amounts may be strings ("12.50", "$12.50"); the API parses them
 * and answers 422 `<field>_invalid` if it can't. null clears a field.
 */
export type EditableFields = Partial<
  Record<"store" | "date" | "category", string | null> & Record<"subtotal" | "gst" | "pst" | "total", number | string | null>
>;

export interface ReceiptPage {
  receipts: Receipt[];
  nextToken?: string;
}

export interface PresignedPost {
  url: string;
  fields: Record<string, string>;
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly reasons: string[] = [],
  ) {
    super(message);
  }
}

export class ApiClient {
  constructor(
    private readonly baseUrl: string,
    private readonly getToken: () => Promise<string>,
  ) {}

  listReceipts(params: { month?: string; status?: string; nextToken?: string }): Promise<ReceiptPage> {
    const query = new URLSearchParams();
    for (const [key, value] of Object.entries(params)) if (value) query.set(key, value);
    const suffix = query.toString() ? `?${query}` : "";
    return this.request<ReceiptPage>("GET", `receipts${suffix}`);
  }

  /** null while the photo hasn't reached the pipeline yet. */
  async getReceipt(id: string): Promise<Receipt | null> {
    try {
      return await this.request<Receipt>("GET", `receipts/${encodeURIComponent(id)}`);
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) return null;
      throw err;
    }
  }

  updateReceipt(id: string, fields: EditableFields): Promise<Receipt> {
    return this.request<Receipt>("PATCH", `receipts/${encodeURIComponent(id)}`, fields);
  }

  /** Get a presigned form, then POST the photo straight to S3. Returns the new receipt id. */
  async uploadPhoto(photo: Blob): Promise<string> {
    const { receiptId, upload } = await this.request<{ receiptId: string; upload: PresignedPost }>(
      "POST",
      "uploads",
      { contentType: photo.type },
    );
    const form = new FormData();
    for (const [key, value] of Object.entries(upload.fields)) form.append(key, value);
    form.append("file", photo); // S3 requires the file to be the last field
    const resp = await fetch(upload.url, { method: "POST", body: form });
    if (!resp.ok) throw new ApiError(resp.status, `Upload failed (HTTP ${resp.status})`);
    return receiptId;
  }

  private async request<T>(method: string, path: string, body?: unknown): Promise<T> {
    const resp = await fetch(`${this.baseUrl}${path}`, {
      method,
      headers: {
        Authorization: `Bearer ${await this.getToken()}`,
        ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new ApiError(resp.status, data.error ?? `HTTP ${resp.status}`, data.reasons ?? []);
    return data as T;
  }
}
