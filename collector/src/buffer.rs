use crate::types::MetricPoint;
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

    pub fn peek(&self) -> io::Result<Option<(PathBuf, Vec<MetricPoint>)>> {
        let mut first = None;
        for entry in fs::read_dir(&self.directory)? {
            let path = entry?.path();
            if batch_index(&path).is_some() && first.as_ref().is_none_or(|current| &path < current)
            {
                first = Some(path);
            }
        }
        match first {
            Some(path) => {
                let points = serde_json::from_reader(BufReader::new(File::open(&path)?))?;
                Ok(Some((path, points)))
            }
            None => Ok(None),
        }
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
        let (first, _) = queue.peek().unwrap().unwrap();
        drop(queue);
        let queue = DiskBuffer::open(path.clone(), 1).unwrap();
        let (same, points) = queue.peek().unwrap().unwrap();
        assert_eq!(first, same);
        assert_eq!(points[0].value, 10.0);
        fs::remove_file(same).unwrap();
        let (second, points) = queue.peek().unwrap().unwrap();
        assert_eq!(points[0].value, 20.0);
        fs::remove_file(second).unwrap();
        assert!(queue.peek().unwrap().is_none());
        fs::remove_dir(path).unwrap();
    }
}
