package com.ai.analytics.backend.repository;

import com.ai.analytics.backend.model.FileRelationship;
import com.ai.analytics.backend.model.User;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;

public interface FileRelationshipRepository extends JpaRepository<FileRelationship, Long> {
    List<FileRelationship> findByOwner(User owner);
}
