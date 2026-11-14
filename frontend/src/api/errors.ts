export class ApiError extends Error {
  code: string;
  status: number;
  field: string | null;
  hint: string | null;
  constructor(code: string, message: string, status = 0, field: string | null = null, hint: string | null = null) {
    super(message);
    this.code = code;
    this.status = status;
    this.field = field;
    this.hint = hint;
  }
}
