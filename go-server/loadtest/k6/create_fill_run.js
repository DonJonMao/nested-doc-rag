import http from 'k6/http';
import { check, fail, sleep } from 'k6';

const baseURL = __ENV.BASE_URL || 'http://localhost:8080';

export default function () {
  if (!__ENV.TOKEN || !__ENV.WORKSPACE_ID || !__ENV.FORM_FILE_ID) {
    fail('TOKEN, WORKSPACE_ID, and FORM_FILE_ID are required');
  }
  const payload = {
    workspace_id: __ENV.WORKSPACE_ID,
    form_file_id: __ENV.FORM_FILE_ID,
    target_namespace: __ENV.TARGET_NAMESPACE || 'loadtest',
    global_namespace: __ENV.GLOBAL_NAMESPACE || 'global',
    room_context: __ENV.ROOM_CONTEXT || 'load test room',
    rows: __ENV.ROWS || '4-4',
    judge: false,
    writeback: true,
  };
  const res = http.post(`${baseURL}/api/v1/fill-runs`, JSON.stringify(payload), {
    headers: {
      Authorization: `Bearer ${__ENV.TOKEN}`,
      'Content-Type': 'application/json',
    },
  });
  check(res, { 'fill run created': (r) => r.status === 200 || r.status === 201 || r.status === 202 });
  sleep(1);
}
