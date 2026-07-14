export function createEmptySchema(selectedDbId) {
  return {
    db_id: selectedDbId,
    tables: [],
    relations: [],
  }
}
