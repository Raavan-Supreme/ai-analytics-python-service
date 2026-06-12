package com.ai.analytics.backend.repository;

import com.ai.analytics.backend.model.UploadedFile;
import com.ai.analytics.backend.model.User;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;

public interface UploadedFileRepository extends JpaRepository<UploadedFile, Long> {
    List<UploadedFile> findByOwner(User owner);
}
