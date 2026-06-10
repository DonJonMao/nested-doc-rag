import http from 'k6/http';
import { check, fail, sleep } from 'k6';

const baseURL = __ENV.BASE_URL || 'http://localhost:8080';

export default function () {
  if (!__ENV.TOKEN || !__ENV.WORKSPACE_ID || !__ENV.TEST_FILE_PATH) {
    fail('TOKEN, WORKSPACE_ID, and TEST_FILE_PATH are required');
  }
  const data = {
    workspace_id: __ENV.WORKSPACE_ID,
    file_category: __ENV.FILE_CATEGORY || 'form_template',
    file: http.file(open(__ENV.TEST_FILE_PATH, 'b'), __ENV.TEST_FILE_NAME || 'loadtest.xlsx'),
  };
  const res = http.post(`${baseURL}/api/v1/files`, data, {
    headers: { Authorization: `Bearer ${__ENV.TOKEN}` },
  });
  check(res, { 'upload accepted': (r) => r.status === 200 || r.status === 201 });
  sleep(1);
}
