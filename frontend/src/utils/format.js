export function formatDbType(dbType) {
  return {
    mysql: 'MySQL',
    pg: 'PostgreSQL',
    oracle: 'Oracle',
  }[dbType] || dbType
}
