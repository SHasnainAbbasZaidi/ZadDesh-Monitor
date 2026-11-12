use crate::types::MetricPoint;
use std::collections::BinaryHeap;
use std::fs::{self, File, OpenOptions};
use std::io::{self, BufRead, BufReader, BufWriter, Write};
use std::path::{Path, PathBuf};

pub struct DiskBuffer {
    directory: PathBuf,
    next: u64,
}

impl DiskBuffer {
    pub fn open(directory: PathBuf, batch_size: usize) -> io::Result<Self> {
        fs::create_dir_all(&directory)?;
        let mut next = 0;
        for entry in fs::read_dir(&directory)? {
            let entry = entry?;
            if let Some(index) = batch_index(&entry.path()) {
                next = next.max(index.saturating_add(1));
            }
        }
        let mut buffer = Self { directory, next };
        buffer.import_legacy(batch_size)?;
        Ok(buffer)
    }

    fn import_legacy(&mut self, batch_size: usize) -> io::Result<()> {
        let path = self.directory.join("offline_metrics.jsonl");
        if !path.exists() {
            return Ok(());
        }
        let mut points = Vec::with_capacity(batch_size);
        for line in BufReader::new(File::open(&path)?).lines() {
            points.push(serde_json::from_str(&line?)?);
            if points.len() == batch_size {
                self.persist(&points)?;
                points.clear();
            }
        }
        if !points.is_empty() {
            self.persist(&points)?;
        }
        // A crash before removal can reimport points; the core deduplicates them.
        fs::remove_file(path)
    }

    pub fn persist(&mut self, points: &[MetricPoint]) -> io::Result<()> {
        let name = format!("{:020}", self.next);
        let pending = self.directory.join(format!("{name}.tmp"));
        let ready = self.directory.join(format!("{name}.json"));
        let file = OpenOptions::new()
            .create(true)
            .truncate(true)
            .write(true)
            .open(&pending)?;
        let mut writer = BufWriter::new(file);
        serde_json::to_writer(&mut writer, points)?;
        writer.flush()?;
        writer.get_ref().sync_all()?;
        drop(writer);
        fs::rename(&pending, &ready)?;
        self.next = self
            .next
            .checked_add(1)
            .ok_or_else(|| io::Error::other("spool sequence exhausted"))?;
        Ok(())
    }

    pub fn directory(&self) -> &Path {
        &self.directory
    }

    pub fn peek(
        directory: &Path,
        batch_size: usize,
    ) -> io::Result<(Vec<PathBuf>, Vec<MetricPoint>)> {
        // Only the oldest few files can contribute to this upload. Limit memory even after a long outage.
        const FILE_LIMIT: usize = 16;
        let mut oldest = BinaryHeap::with_capacity(FILE_LIMIT + 1);
        for entry in fs::read_dir(directory)? {
            let path = entry?.path();
            if batch_index(&path).is_some() {
                oldest.push(path);
                if oldest.len() > FILE_LIMIT {
                    oldest.pop();
                }
            }
        }
        let mut paths = Vec::new();
        let mut points = Vec::new();
        for path in oldest.into_sorted_vec() {
            let batch: Vec<MetricPoint> = match File::open(&path).and_then(|file| {
                serde_json::from_reader(BufReader::new(file)).map_err(io::Error::from)
            }) {
                Ok(batch) => batch,
                Err(error) if paths.is_empty() => return Err(error),
                Err(_) => break, // Deliver the valid prefix before reporting the unreadable file.
            };
            if !paths.is_empty() && points.len() + batch.len() > batch_size {
                break;
            }
            paths.push(path);
            points.extend(batch);
            if points.len() >= batch_size {
                break;
            }
        }
        Ok((paths, points))
    }
}

fn batch_index(path: &Path) -> Option<u64> {
    if path.extension()?.to_str()? != "json" {
        return None;
    }
    path.file_stem()?.to_str()?.parse().ok()
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::Utc;
    use std::collections::HashMap;

    #[test]
    fn unacknowledged_batch_survives_restart_and_stays_first() {
        let path = std::env::temp_dir().join(format!(
            "zaddesh-spool-{}-{}",
            std::process::id(),
            Utc::now().timestamp_nanos_opt().unwrap()
        ));
        let point = |value| MetricPoint {
            device_id: 1,
            check_id: Some(1),
            metric_key: "test".into(),
            timestamp: Utc::now(),
            value,
            tags: HashMap::new(),
        };
        let mut queue = DiskBuffer::open(path.clone(), 1).unwrap();
        queue.persist(&[point(10.0)]).unwrap();
        queue.persist(&[point(20.0)]).unwrap();
        let (first, _) = DiskBuffer::peek(&path, 1).unwrap();
        drop(queue);
        let queue = DiskBuffer::open(path.clone(), 1).unwrap();
        let (same, points) = DiskBuffer::peek(queue.directory(), 1).unwrap();
        assert_eq!(first, same);
        assert_eq!(points[0].value, 10.0);
        let (merged, points) = DiskBuffer::peek(&path, 2).unwrap();
        assert_eq!(merged.len(), 2);
        assert_eq!(merged[0], first[0]);
        assert_eq!([points[0].value, points[1].value], [10.0, 20.0]);
        fs::remove_file(&same[0]).unwrap();
        let (second, points) = DiskBuffer::peek(&path, 1).unwrap();
        assert_eq!(points[0].value, 20.0);
        fs::remove_file(&second[0]).unwrap();
        assert!(DiskBuffer::peek(&path, 1).unwrap().0.is_empty());
        fs::remove_dir(path).unwrap();
    }
}
