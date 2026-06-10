import http from 'k6/http';
import { check, sleep } from 'k6';

export const options = {
  vus: Number(__ENV.VUS || 1),
  iterations: Number(__ENV.ITERATIONS || 1),
};

const baseURL = __ENV.BASE_URL || 'http://localhost:8080';

export default function () {
  const ping = http.get(`${baseURL}/api/v1/ping`);
  check(ping, { 'ping ok': (r) => r.status === 200 });

  const health = http.get(`${baseURL}/healthz`);
  check(health, { 'health ok': (r) => r.status === 200 });

  if (__ENV.TOKEN) {
    const workspaces = http.get(`${baseURL}/api/v1/workspaces`, {
      headers: { Authorization: `Bearer ${__ENV.TOKEN}` },
    });
    check(workspaces, { 'workspaces authorized': (r) => r.status < 500 });
  }
  sleep(1);
}
