export interface BsFormState {
  accountText: string;
  policyText: string;
  studyText: string;
  enhancedId: string;
  newAttempt: boolean;
  cutoff: string;
  dateFrom: string;
  dateTo: string;
  cohort: "rolling" | "fixed";
}

export type BsMode = "advice" | "review" | "experiment";

export interface BsNotice {
  tone: "info" | "warning" | "error";
  text: string;
}
