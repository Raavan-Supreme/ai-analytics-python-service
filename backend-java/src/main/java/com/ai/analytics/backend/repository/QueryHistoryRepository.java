package com.ai.analytics.backend.repository;

import com.ai.analytics.backend.model.QueryHistory;
import com.ai.analytics.backend.model.User;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;

public interface QueryHistoryRepository extends JpaRepository<QueryHistory, Long> {
    List<QueryHistory> findByOwnerOrderByCreatedAtDesc(User owner, Pageable pageable);
}
