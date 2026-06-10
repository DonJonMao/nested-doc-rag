import http from 'k6/http';
import { check, fail, sleep } from 'k6';

export const options = {
  vus: Number(__ENV.VUS || 5),
  duration: __ENV.DURATION || '30s',
  thresholds: {
    http_req_failed: ['rate<0.05'],
    http_req_duration: ['p(95)<1000'],
  },
};

const baseURL = __ENV.BASE_URL || 'http://localhost:8080';

export default function () {
  if (!__ENV.USERNAME || !__ENV.PASSWORD) {
    fail('USERNAME and PASSWORD are required');
  }
  const res = http.post(
    `${baseURL}/api/v1/auth/login`,
    JSON.stringify({ username: __ENV.USERNAME, password: __ENV.PASSWORD }),
    { headers: { 'Content-Type': 'application/json' } },
  );
  check(res, {
    'login status 200': (r) => r.status === 200,
    'access token returned': (r) => String(r.body).includes('access_token'),
  });
  sleep(1);
}
