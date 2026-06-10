import http from 'k6/http';
import { check, fail } from 'k6';

export const options = {
  vus: Number(__ENV.VUS || 1),
  duration: __ENV.DURATION || '15s',
};

const baseURL = __ENV.BASE_URL || 'http://localhost:8080';

export default function () {
  if (!__ENV.TOKEN || !__ENV.RUN_ID || !__ENV.WORKSPACE_ID) {
    fail('TOKEN, RUN_ID, and WORKSPACE_ID are required');
  }
  const url = `${baseURL}/api/v1/runs/${__ENV.RUN_ID}/events?workspace_id=${__ENV.WORKSPACE_ID}`;
  const res = http.get(url, {
    headers: {
      Authorization: `Bearer ${__ENV.TOKEN}`,
      Accept: 'text/event-stream',
    },
    timeout: __ENV.SSE_TIMEOUT || '20s',
  });
  check(res, {
    'sse accepted': (r) => r.status === 200 || r.status === 204,
  });
}
