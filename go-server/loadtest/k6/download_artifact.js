import http from 'k6/http';
import { check, fail, sleep } from 'k6';

export const options = {
  vus: Number(__ENV.VUS || 3),
  duration: __ENV.DURATION || '30s',
  thresholds: {
    http_req_failed: ['rate<0.05'],
  },
};

const baseURL = __ENV.BASE_URL || 'http://localhost:8080';

export default function () {
  if (!__ENV.TOKEN || !__ENV.ARTIFACT_URL) {
    fail('TOKEN and ARTIFACT_URL are required');
  }
  const url = __ENV.ARTIFACT_URL.startsWith('http') ? __ENV.ARTIFACT_URL : `${baseURL}${__ENV.ARTIFACT_URL}`;
  const res = http.get(url, {
    headers: { Authorization: `Bearer ${__ENV.TOKEN}` },
    responseType: 'none',
  });
  check(res, {
    'download ok': (r) => r.status === 200,
  });
  sleep(1);
}
