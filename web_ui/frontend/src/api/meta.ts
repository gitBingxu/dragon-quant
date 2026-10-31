import { getJson } from "./client";

export interface Meta {
  version: string;
  data_dir: string;
  dragon_db: boolean;
  assets_db: boolean;
  dist_built: boolean;
  sources: string[];
  default_source: string;
  default_page: string;
  routes: string[];
}

export function fetchMeta() {
  return getJson<Meta>("/api/meta");
}
